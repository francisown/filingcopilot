"""Locate exact-target misses without changing frozen gold or passing it to search."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from collections import Counter
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings


def run(retriever, items, *, query_mode, candidate_limit):
    rows = []
    started = time.perf_counter()
    for item in items:
        stamp = time.perf_counter()
        result = retriever.search(item['query'], date(2026, 10, 10), item['ticker'], 8,
                                  item['fiscal_year'], query_mode=query_mode,
                                  candidate_limit=candidate_limit, diagnostics=True)
        expected = set(item['expected_passage_ids'])
        candidates = result['retrieval_diagnostics']['candidate_ids']
        hits = [hit['passage']['id'] for hit in result['hits'][:5]]
        rank = next((i for i, eid in enumerate(hits, 1) if eid in expected), None)
        available = {p.id for p in retriever.corpus.passages(date(2026, 10, 10), item['ticker'], item['fiscal_year'])}
        rows.append({'id': item['id'], 'language': item['language'],
                     'source_missing': not bool(expected & available),
                     'candidate_miss': not bool(expected & set(candidates)),
                     'recall': rank is not None, 'mrr': 1 / rank if rank else 0,
                     'candidate_target_rank': next((i for i, eid in enumerate(candidates, 1) if eid in expected), None),
                     'query_rewrite': result['query_rewrite'], 'backend': result['backend'],
                     'embedding_unavailable': result['embedding_unavailable'],
                     'elapsed_ms': round((time.perf_counter() - stamp) * 1000, 3)})
    n = len(rows)
    durations = sorted(row['elapsed_ms'] for row in rows)
    return {'query_mode': query_mode, 'candidate_limit': candidate_limit, 'questions': n,
            'recall_at_5': round(sum(row['recall'] for row in rows) / n, 6),
            'mrr_at_5': round(sum(row['mrr'] for row in rows) / n, 6),
            'source_missing': sum(row['source_missing'] for row in rows),
            'candidate_misses': sum(row['candidate_miss'] for row in rows),
            'ranking_misses': sum(not row['recall'] and not row['candidate_miss'] for row in rows),
            'embedding_errors': dict(Counter(row['embedding_unavailable'] for row in rows if row['embedding_unavailable'])),
            'p50_ms': durations[n // 2], 'p95_ms': durations[min(n - 1, int(n * .95))],
            'total_seconds': round(time.perf_counter() - started, 3), 'rows': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path('.filingcopilot'))
    parser.add_argument('--gold', type=Path, default=Path('evaluation/retrieval_gold_v2.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-snapshot', action='store_true', help='Keep the immutable database copy for forensic inspection')
    args = parser.parse_args()
    raw = args.gold.read_bytes()
    items = json.loads(raw)['items']
    # Keep ingestion or other web jobs from changing the candidate universe
    # between controls. Models/vectors remain in the original home; only the
    # evidence database is backed up into a fresh immutable evaluation scope.
    snapshot = args.home / 'retrieval-diagnostic-snapshots' / uuid.uuid4().hex
    snapshot.mkdir(parents=True)
    snapshot_db = snapshot / 'corpus.sqlite3'
    with sqlite3.connect(args.home / 'corpus.sqlite3') as original, sqlite3.connect(snapshot_db) as frozen:
        original.backup(frozen)
    source_sha = hashlib.sha256(snapshot_db.read_bytes()).hexdigest()
    retriever = Retriever(Corpus(snapshot, readonly=True), Settings(home=args.home))
    # Warm only the first source scope as in the previous calibrated runs.
    first = items[0]
    warm = retriever.search(first['query'], date(2026, 10, 10), first['ticker'], 8, first['fiscal_year'])
    if warm['embedding_unavailable']:
        raise RuntimeError('Hybrid unavailable; refuse a misleading candidate comparison')
    variants = {}
    for label, mode, limit in [('baseline', 'full', 25), ('focused', 'focused', 25), ('wider', 'full', 75), ('focused_wider', 'focused', 75)]:
        variants[label] = run(retriever, items, query_mode=mode, candidate_limit=limit)
        print(json.dumps({'variant': label, **{k:v for k,v in variants[label].items() if k!='rows'}}, ensure_ascii=False), flush=True)
    baseline, candidate = variants['baseline'], variants['focused_wider']
    gate = (candidate['recall_at_5'] > baseline['recall_at_5']
            and candidate['mrr_at_5'] > baseline['mrr_at_5']
            and candidate['p95_ms'] <= max(500, baseline['p95_ms'] * 1.2)
            and not candidate['embedding_errors'])
    report = {'schema': 'filingcopilot.retrieval-diagnosis/v1', 'gold_sha256': hashlib.sha256(raw).hexdigest(),
              'source_snapshot_sha256': source_sha, 'source_snapshot': str(snapshot_db),
              'source_snapshot_unchanged': hashlib.sha256(snapshot_db.read_bytes()).hexdigest() == source_sha,
              'focused_wider_activation_gate': {'passed': gate, 'recall_and_mrr_must_improve': True,
                                                'p95_limit_ms': max(500, baseline['p95_ms'] * 1.2)},
              'full_set': True, 'variants': variants,
              'notes': ['Frozen editorial exact-passage calibration, not human semantic accuracy.',
                        'No expected ID or gold_context is supplied to retrieval; controls isolate query text and candidate depth.',
                        'All variants use one database backup; concurrent ingestion cannot alter the candidate universe.',
                        'Timing includes the scoped corpus availability check and runs on a shared Windows host.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if not args.keep_snapshot:
        shutil.rmtree(snapshot, ignore_errors=True)
        report['source_snapshot_retained'] = False
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
