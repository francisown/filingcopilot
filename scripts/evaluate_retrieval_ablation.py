"""Run a reproducible retrieval ablation against a frozen gold JSON file."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings


def score(item: dict, result: dict, k: int) -> tuple[float, float]:
    hits = result.get("hits", [])[:k]
    expected = set(item.get("expected_passage_ids", []))
    if expected:
        ranks = [rank for rank, hit in enumerate(hits, 1) if hit["passage"]["id"] in expected]
    else:
        section = item.get("gold_context", {}).get("section", "")
        terms = [str(term).casefold() for term in item.get("gold_context", {}).get("anchor_terms", [])]
        ranks = [
            rank
            for rank, hit in enumerate(hits, 1)
            if section.casefold() in hit["passage"].get("section", "").casefold()
            or sum(term in hit["passage"].get("text", "").casefold() for term in terms) >= 2
        ]
    return (1.0 if ranks else 0.0, 1.0 / ranks[0] if ranks else 0.0)


def run_variant(
    retriever: Retriever,
    items: list[dict],
    *,
    lexical_only: bool,
    rerank_mode: str = "anchor",
    anchor_weight: float = 0.01,
) -> dict:
    recalls, mrrs, durations = [], [], []
    backends = Counter()
    dense_hit_rows = 0
    embedding_errors = Counter()
    for item in items:
        started = time.perf_counter()
        result = retriever.search(
            item["query"],
            date(2026, 10, 10),
            ticker=item["ticker"],
            k=5,
            fiscal_year=item["fiscal_year"],
            lexical_only=lexical_only,
            rerank_mode=rerank_mode,
            anchor_weight=anchor_weight,
        )
        backends[result["backend"]] += 1
        dense_hit_rows += any("dense" in hit.get("legs", {}) for hit in result.get("hits", []))
        if result.get("embedding_unavailable"):
            embedding_errors[result["embedding_unavailable"]] += 1
        recalls.append(score(item, result, 5)[0])
        mrrs.append(score(item, result, 5)[1])
        durations.append((time.perf_counter() - started) * 1000)
    n = len(items) or 1
    durations.sort()
    return {
        "questions": len(items),
        "recall_at_5": round(sum(recalls) / n, 6),
        "mrr_at_5": round(sum(mrrs) / n, 6),
        "backend_counts": dict(backends),
        "rows_with_dense_hit": dense_hit_rows,
        "embedding_errors": dict(embedding_errors),
        "p50_ms": round(durations[len(durations) // 2], 3) if durations else 0,
        "p95_ms": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 3)
        if durations
        else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(".filingcopilot"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/retrieval_gold_v2.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/retrieval-ablation.json"))
    parser.add_argument("--limit", type=int, help="Run a deterministic prefix for a quick pilot")
    parser.add_argument("--embedding-mode", choices=("local", "off"), default="local")
    parser.add_argument("--rerank-mode", choices=("rrf", "anchor"), default="anchor")
    parser.add_argument(
        "--anchor-weight",
        type=float,
        default=0.01,
        help="Deterministic anchor coverage bonus; recorded for reproducibility.",
    )
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    items = gold["items"][: args.limit] if args.limit else gold["items"]
    retriever = Retriever(Corpus(args.home), Settings(home=args.home, embedding_mode=args.embedding_mode))
    fts = run_variant(
        retriever,
        items,
        lexical_only=True,
        rerank_mode=args.rerank_mode,
        anchor_weight=args.anchor_weight,
    )
    hybrid = run_variant(
        retriever,
        items,
        lexical_only=False,
        rerank_mode=args.rerank_mode,
        anchor_weight=args.anchor_weight,
    )
    dense_available = hybrid["backend_counts"].get("fts5+bge+rrf", 0) > 0
    recall_delta = round(hybrid["recall_at_5"] - fts["recall_at_5"], 6)
    mrr_delta = round(hybrid["mrr_at_5"] - fts["mrr_at_5"], 6)
    improved = dense_available and recall_delta > 0 and mrr_delta >= 0
    tradeoff = dense_available and recall_delta > 0 and mrr_delta < 0
    baseline = None
    baseline_delta = None
    baseline_improved = False
    baseline_tradeoff = False
    if args.rerank_mode == "anchor":
        baseline_fts = run_variant(
            retriever, items, lexical_only=True, rerank_mode="rrf", anchor_weight=0.0
        )
        baseline_hybrid = run_variant(
            retriever, items, lexical_only=False, rerank_mode="rrf", anchor_weight=0.0
        )
        baseline = {"rerank_mode": "rrf", "fts5": baseline_fts, "hybrid_rrf": baseline_hybrid}
        baseline_recall_delta = round(
            hybrid["recall_at_5"] - baseline_hybrid["recall_at_5"], 6
        )
        baseline_mrr_delta = round(hybrid["mrr_at_5"] - baseline_hybrid["mrr_at_5"], 6)
        baseline_delta = {
            "recall_at_5": baseline_recall_delta,
            "mrr_at_5": baseline_mrr_delta,
        }
        baseline_improved = baseline_recall_delta > 0 and baseline_mrr_delta > 0
        baseline_tradeoff = baseline_recall_delta > 0 and baseline_mrr_delta < 0
    result = {
        "schema": "filingcopilot.retrieval-ablation/v1",
        "gold": {"path": str(args.gold), "version": gold.get("version"), "questions": len(items)},
        "retriever": {
            "embedding_mode": args.embedding_mode,
            "rerank_mode": args.rerank_mode,
            "anchor_weight": args.anchor_weight,
            "k": 5,
        },
        "variants": {
            "fts5": fts,
            "hybrid_rrf": hybrid,
        },
        "hybrid_delta": {
            "recall_at_5": recall_delta,
            "mrr_at_5": mrr_delta,
        },
        **({"baseline": baseline} if baseline else {}),
        **({"baseline_delta": baseline_delta} if baseline_delta else {}),
        "not_integrated": [
            "neural_query_rewrite",
            "multilingual_embedding",
            "cross_encoder_rerank",
        ],
        "decision": (
            "lexical_only_baseline_not_a_hybrid_comparison"
            if args.embedding_mode == "off"
            else "enable_anchor_rerank"
            if baseline_improved
            else "anchor_rerank_recall_gain_mrr_tradeoff"
            if baseline_tradeoff
            else "anchor_rerank_no_reproducible_gain"
            if baseline is not None
            else "retain_hybrid_rrf_with_reproducible_gain"
            if improved
            else "hybrid_recall_gain_mrr_tradeoff"
            if tradeoff
            else "retain_hybrid_rrf_no_reproducible_gain"
            if dense_available
            else "hybrid_unavailable"
        ),
        "notes": [
            "The hybrid variant is only comparable when its backend reports fts5+bge+rrf.",
            "Use --limit for a bounded pilot; local embedding generation is intentionally measured in the report.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
