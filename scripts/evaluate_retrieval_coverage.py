"""Evaluate the deterministic coverage reranker on the frozen retrieval gold set.

This script intentionally uses no model, network, embeddings, or expected IDs
inside retrieval. The gold IDs are used only for scoring after the ranked list
is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings


def _score(item: dict, result: dict, k: int = 5) -> tuple[bool, float]:
    expected = set(item["expected_passage_ids"])
    hits = [hit["passage"]["id"] for hit in result.get("hits", [])[:k]]
    rank = next((index for index, pid in enumerate(hits, 1) if pid in expected), None)
    return rank is not None, 1 / rank if rank else 0.0


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


def _summary(rows: list[dict]) -> dict:
    n = len(rows) or 1
    durations = sorted(row["elapsed_ms"] for row in rows)
    return {
        "questions": len(rows),
        "recall_at_5": round(sum(row["recall"] for row in rows) / n, 6),
        "mrr_at_5": round(sum(row["mrr"] for row in rows) / n, 6),
        "candidate_misses": sum(row["candidate_miss"] for row in rows),
        "p50_ms": round(durations[len(durations) // 2], 3) if durations else 0,
        "p95_ms": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 3)
        if durations
        else 0,
        "backend_counts": dict(Counter(row["backend"] for row in rows)),
        "by_language": {
            language: {
                "questions": len(group),
                "recall_at_5": round(sum(row["recall"] for row in group) / len(group), 6),
                "mrr_at_5": round(sum(row["mrr"] for row in group) / len(group), 6),
            }
            for language, group in _group(rows, "language").items()
        },
        "by_section": {
            section: {
                "questions": len(group),
                "recall_at_5": round(sum(row["recall"] for row in group) / len(group), 6),
                "mrr_at_5": round(sum(row["mrr"] for row in group) / len(group), 6),
            }
            for section, group in _group(rows, "section").items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(".filingcopilot"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/retrieval_gold_v2.json"))
    parser.add_argument("--as-of", default="2026-10-10")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=Path("artifacts/retrieval-coverage.json"))
    args = parser.parse_args()

    raw = args.gold.read_bytes()
    gold = json.loads(raw)
    items = gold["items"][: args.limit] if args.limit else gold["items"]
    as_of = date.fromisoformat(args.as_of)
    corpus = Corpus(args.home)
    settings = Settings(home=args.home, embedding_mode="off", rerank_mode="coverage")
    retriever = Retriever(corpus, settings)

    rows = []
    for item in items:
        started = time.perf_counter()
        result = retriever.search(
            item["query"],
            as_of,
            ticker=item["ticker"],
            k=5,
            fiscal_year=item["fiscal_year"],
            diagnostics=True,
        )
        recall, mrr = _score(item, result)
        candidates = set(result["retrieval_diagnostics"]["candidate_ids"])
        rows.append(
            {
                "id": item["id"],
                "language": item["language"],
                "ticker": item["ticker"],
                "fiscal_year": item["fiscal_year"],
                "section": item.get("gold_context", {}).get("section", ""),
                "recall": recall,
                "mrr": mrr,
                "candidate_miss": not bool(set(item["expected_passage_ids"]) & candidates),
                "backend": result["backend"],
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
        )

    summary = _summary(rows)
    report = {
        "schema": "filingcopilot.retrieval-coverage/v1",
        "gold_sha256": hashlib.sha256(raw).hexdigest(),
        "settings": {
            "rerank_mode": settings.rerank_mode,
            "embedding_mode": settings.embedding_mode,
            "query_mode": settings.retrieval_query_mode,
            "candidate_limit": settings.retrieval_candidate_limit,
        },
        "summary": summary,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
