"""Compare an optional neural reranker or embedding model on frozen evidence IDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import getproxies

from filingcopilot.corpus import Corpus
from filingcopilot.local_models import verify_model_snapshot
from filingcopilot.reranking import rerank_candidates
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings


def metrics(rows):
    durations = sorted(row["elapsed_ms"] for row in rows)
    n = len(rows)
    return {
        "questions": n,
        "recall_at_5": sum(row["recall"] for row in rows) / n,
        "mrr_at_5": sum(row["mrr"] for row in rows) / n,
        "p50_ms": durations[n // 2],
        "p95_ms": durations[min(n - 1, int(n * 0.95))],
        "failures": sum(bool(row.get("error")) for row in rows),
    }


def score(hits, expected):
    ranks = [i for i, hit in enumerate(hits[:5], 1) if hit["passage"]["id"] in expected]
    return (bool(ranks), 1 / ranks[0] if ranks else 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(".filingcopilot"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/retrieval_gold_v2.json"))
    parser.add_argument("--cross-encoder", default="Xenova/ms-marco-MiniLM-L-6-v2")
    parser.add_argument("--cross-encoder-path", type=Path)
    parser.add_argument("--embedding-model-path", type=Path)
    parser.add_argument(
        "--embedding-model", help="Compare this candidate to the calibrated English BGE baseline"
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-p95-ms", type=float, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.embedding_model_path and not args.embedding_model:
        parser.error("--embedding-model-path requires --embedding-model")
    if args.cross_encoder_path and args.embedding_model:
        parser.error("--cross-encoder-path cannot be combined with --embedding-model")
    # Honor the host's configured proxy without weakening TLS or logging credentials.
    for scheme, address in getproxies().items():
        if scheme in ("http", "https"):
            os.environ.setdefault(f"{scheme.upper()}_PROXY", address)
    raw = args.gold.read_bytes()
    gold = json.loads(raw)
    items = gold["items"][: args.limit] if args.limit else gold["items"]
    if not items:
        raise ValueError("Evaluation requires nonempty frozen questions")
    corpus = Corpus(args.home)
    for item in items:
        eligible = {
            p.id for p in corpus.passages(date(2026, 10, 10), item["ticker"], item["fiscal_year"])
        }
        if not set(item["expected_passage_ids"]) <= eligible:
            raise ValueError("Frozen gold IDs are missing from the scoped corpus")
    baseline = Retriever(corpus, Settings(home=args.home, embedding_mode="local"))
    candidate = (
        Retriever(
            corpus,
            Settings(home=args.home, embedding_mode="local", embedding_model=args.embedding_model),
            model_path=args.embedding_model_path,
        )
        if args.embedding_model
        else baseline
    )
    scorer = None
    cold_started = time.perf_counter()
    setup_error = None
    try:
        warm = items[0]
        baseline.search(warm["query"], date(2026, 10, 10), warm["ticker"], 8, warm["fiscal_year"])
        if not args.embedding_model:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            if args.cross_encoder_path:
                verify_model_snapshot(args.cross_encoder_path, args.cross_encoder)
            scorer = TextCrossEncoder(
                args.cross_encoder, cache_dir=str(args.home / "models"), threads=2,
                **({"specific_model_path": str(args.cross_encoder_path)} if args.cross_encoder_path else {}),
            )
        candidate_warm = candidate.search(
            warm["query"], date(2026, 10, 10), warm["ticker"], 8, warm["fiscal_year"]
        )
        if candidate_warm.get("embedding_unavailable"):
            setup_error = "EmbeddingUnavailable"
        if scorer:
            list(scorer.rerank("business", ["Business operations and revenue."]))
    except Exception as exc:
        setup_error = type(exc).__name__
    cold_ms = round((time.perf_counter() - cold_started) * 1000, 3)
    lexical_rows, baseline_rows, candidate_rows = [], [], []
    for item in items:
        expected = set(item["expected_passage_ids"])
        started = time.perf_counter()
        lexical = baseline.search(
            item["query"], date(2026, 10, 10), item["ticker"], 8, item["fiscal_year"],
            lexical_only=True,
        )
        recall, mrr = score(lexical["hits"], expected)
        lexical_rows.append({
            "id": item["id"], "recall": recall, "mrr": mrr,
            "elapsed_ms": (time.perf_counter() - started) * 1000, "error": None,
        })
        started = time.perf_counter()
        result = baseline.search(
            item["query"], date(2026, 10, 10), item["ticker"], 8, item["fiscal_year"]
        )
        elapsed = (time.perf_counter() - started) * 1000
        recall, mrr = score(result["hits"], expected)
        baseline_rows.append(
            {
                "id": item["id"],
                "recall": recall,
                "mrr": mrr,
                "elapsed_ms": elapsed,
                "error": result.get("embedding_unavailable"),
            }
        )
        started = time.perf_counter()
        if args.embedding_model:
            trial = candidate.search(
                item["query"], date(2026, 10, 10), item["ticker"], 8, item["fiscal_year"]
            )
            trial_error = trial.get("embedding_unavailable")
        elif scorer:
            trial = rerank_candidates(result["query"], result["hits"], scorer)
            trial_error = trial["error"]
        else:
            trial, trial_error = result, setup_error or "ScorerUnavailable"
        extra = (time.perf_counter() - started) * 1000
        recall, mrr = score(trial["hits"], expected)
        candidate_rows.append(
            {
                "id": item["id"],
                "recall": recall,
                "mrr": mrr,
                "elapsed_ms": extra if args.embedding_model else elapsed + extra,
                "error": trial_error,
            }
        )
        if len(candidate_rows) % 25 == 0:
            print(json.dumps({"completed": len(candidate_rows), "total": len(items)}), flush=True)
    left, right = metrics(baseline_rows), metrics(candidate_rows)
    full = len(items) == len(gold["items"])
    accepted = (
        full
        and not setup_error
        and not right["failures"]
        and not left["failures"]
        and right["recall_at_5"] > left["recall_at_5"]
        and right["mrr_at_5"] > left["mrr_at_5"]
        and right["p95_ms"] <= args.max_p95_ms
    )
    report = {
        "schema": "filingcopilot.retrieval-candidate-evaluation/v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "gold_sha256": hashlib.sha256(raw).hexdigest(),
        "gold_version": gold["version"],
        "full_set": full,
        "candidate_window": 8,
        "cold_setup_ms": cold_ms,
        "setup_error": setup_error,
        "status": "candidate_unavailable" if setup_error or right["failures"] or left["failures"]
        else "scored" if full else "partial_set",
        "fts5": {"query_rewrite": "domain+abbreviations", "rerank": "anchor", **metrics(lexical_rows)},
        "baseline": {"embedding_model": "BAAI/bge-small-en-v1.5", "rerank": "anchor", **left},
        "candidate": {
            "embedding_model": args.embedding_model or "BAAI/bge-small-en-v1.5",
            "rerank": "anchor" if args.embedding_model else args.cross_encoder,
            "quality_measurement_valid": not setup_error and not right["failures"],
            "score_source": "candidate" if not setup_error and not right["failures"] else "fallback; not candidate quality",
            **right,
        },
        "threshold": {
            "recall_strictly_improves": True,
            "mrr_strictly_improves": True,
            "max_p95_ms": args.max_p95_ms,
        },
        "decision": "eligible_for_production_integration"
        if accepted
        else "retain_calibrated_baseline",
        "valid_neural_comparison": not setup_error
        and not right["failures"]
        and not left["failures"],
        "rows": [
            {"id": a["id"], "fts5": fts, "baseline": a, "candidate": b}
            for fts, a, b in zip(lexical_rows, baseline_rows, candidate_rows, strict=True)
        ],
        "notes": [
            "Candidate reorder uses only the eight already scoped baseline candidates.",
            "Gold set is corpus-anchored calibration, not human semantic accuracy.",
            "No runtime default is changed by this script.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False))
    if not report["valid_neural_comparison"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
