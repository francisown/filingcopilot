"""Fixed-pool reranking ablation on the previous immutable 200-question corpus."""

import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.eval_support import require_file
from filingcopilot.local_models import verify_model_snapshot
from filingcopilot.reranking import rerank_candidates, scoped_candidate_hits
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings


def summary(rows):
    times = sorted(row['elapsed_ms'] for row in rows)
    return {'questions': len(rows), 'recall_at_5': sum(r['recall'] for r in rows)/len(rows),
            'mrr_at_5': sum(r['mrr'] for r in rows)/len(rows), 'p50_ms': times[len(rows)//2],
            'p95_ms': times[min(len(rows)-1, int(len(rows)*.95))],
            'errors': sum(bool(r['error']) for r in rows)}


def scored(item_id, hits, expected, elapsed, error):
    rank = next((i for i, hit in enumerate(hits[:5], 1) if hit['passage']['id'] in expected), None)
    return {'id': item_id, 'recall': rank is not None, 'mrr': 1/rank if rank else 0,
            'elapsed_ms': round(elapsed, 3), 'error': error}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path('.filingcopilot'))
    parser.add_argument('--gold', type=Path, default=Path('evaluation/retrieval_gold_v2.json'))
    parser.add_argument('--frozen-diagnosis', type=Path, default=Path('artifacts/retrieval-frozen-diagnosis-2026-10-11.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require_file(args.gold, "retrieval gold")
    require_file(args.frozen_diagnosis, "frozen retrieval diagnosis")
    raw = args.gold.read_bytes()
    old = json.loads(args.frozen_diagnosis.read_text(encoding='utf-8'))
    snapshot = require_file(Path(old['source_snapshot']), "frozen source snapshot")
    source_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    # Prior 1.0.3 diagnosis recorded the backup hash before Corpus constructor
    # incremented SQLite's header counter. Accept only that exact two-field
    # header difference; any evidence/schema byte change remains a hard error.
    prior = bytearray(snapshot.read_bytes())
    header_only = False
    if source_sha != old['source_snapshot_sha256']:
        for offset in (24, 92):
            counter = int.from_bytes(prior[offset:offset+4], 'big')
            if counter < 1:
                raise ValueError('Invalid SQLite header counter')
            prior[offset:offset+4] = (counter-1).to_bytes(4, 'big')
        header_only = hashlib.sha256(prior).hexdigest() == old['source_snapshot_sha256']
    if (source_sha != old['source_snapshot_sha256'] and not header_only) or hashlib.sha256(raw).hexdigest() != old['gold_sha256']:
        raise ValueError('Frozen data/gold hashes changed')
    corpus = Corpus(snapshot.parent, readonly=True)
    retriever = Retriever(corpus, Settings(home=args.home))
    model_name = 'Xenova/ms-marco-MiniLM-L-6-v2'
    model_path = args.home / 'models' / 'cross-encoder-local'
    manifest = verify_model_snapshot(model_path, model_name)
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    stamp = time.perf_counter()
    scorer = TextCrossEncoder(model_name, specific_model_path=str(model_path), threads=2)
    list(scorer.rerank('business', ['Business operations and revenue.']))
    items = json.loads(raw)['items']
    first = items[0]
    retriever.search(first['query'], date(2026, 10, 10), first['ticker'], 8, first['fiscal_year'])
    setup_ms = round((time.perf_counter()-stamp)*1000, 3)
    variants = {'baseline': [], 'rerank_public_eight': [], 'rerank_full_pool': []}
    pools = []
    for item in items:
        stamp = time.perf_counter()
        result = retriever.search(item['query'], date(2026, 10, 10), item['ticker'], 8,
                                  item['fiscal_year'], diagnostics=True)
        retrieval_ms = (time.perf_counter()-stamp)*1000
        # Never pass expected IDs or editorial context to any retrieval/scorer.
        expected = set(item['expected_passage_ids'])
        variants['baseline'].append(scored(item['id'], result['hits'], expected, retrieval_ms, result['embedding_unavailable']))
        for label in ('rerank_public_eight', 'rerank_full_pool'):
            stamp = time.perf_counter()
            pool = (scoped_candidate_hits(corpus, result, date(2026, 10, 10), item['ticker'], item['fiscal_year'])
                    if label == 'rerank_full_pool' else result['hits'])
            trial = rerank_candidates(result['query'], pool, scorer)
            elapsed = retrieval_ms+(time.perf_counter()-stamp)*1000
            variants[label].append(scored(item['id'], trial['hits'], expected, elapsed, trial['error']))
        pools.append({'id': item['id'], 'candidate_count': len(pool),
                      'candidate_miss': not bool(expected & {h['passage']['id'] for h in pool})})
        if len(pools)%25 == 0:
            print(json.dumps({'completed': len(pools), 'total': len(items)}), flush=True)
    sums = {name: summary(rows) for name, rows in variants.items()}
    base, full = sums['baseline'], sums['rerank_full_pool']
    accepted = (full['recall_at_5'] > base['recall_at_5'] and full['mrr_at_5'] > base['mrr_at_5']
                and full['p95_ms'] <= 1000 and not any(v['errors'] for v in sums.values()))
    unchanged = hashlib.sha256(snapshot.read_bytes()).hexdigest() == source_sha
    report = {'schema': 'filingcopilot.full-pool-rerank-ablation/v1', 'full_set': len(items)==200,
              'gold_sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': source_sha,
              'source_unchanged': unchanged, 'model_name': model_name, 'model_manifest': manifest,
              'prior_recorded_sha256': old['source_snapshot_sha256'],
              'prior_hash_difference_verified_header_only': header_only,
              'cold_setup_ms': setup_ms, 'settings': {'query_mode': 'focused', 'candidates_per_leg': 75},
              'summaries': sums, 'rows': variants, 'pools': pools,
              'gate': {'passed': accepted and unchanged, 'strict_recall_mrr_improvement': True, 'max_p95_ms': 1000},
              'decision': 'eligible_for_integration' if accepted and unchanged else 'retain_current_retrieval',
              'notes': ['Editorial exact-passage calibration; no human semantic labels.',
                        'Existing pinned weights only; no production configuration changed.',
                        'Public-eight and entire union share one scoped result per question.',
                        'Latency includes search, pool resolution and reranking, excludes one-time setup.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'summaries': sums, 'gate': report['gate'], 'decision': report['decision']}), flush=True)


if __name__ == '__main__':
    main()
