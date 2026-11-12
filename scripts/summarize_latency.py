"""Summarize author/review latency from a frozen evaluation results file.

The evaluation runner already records ``author_ms`` and ``review_ms`` in each
row's ``trace_summary``. This script turns those fields into the numbers used in
the latency budget: P50/P95, review share and per-category tails. It never calls
a model and never changes the result file.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def _percentile(values: list[float], fraction: float) -> float:
    """Use the same nearest-rank convention as evaluate_semantic_set.py."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50": round(statistics.median(values), 1) if values else 0.0,
        "p95": round(_percentile(values, 0.95), 1),
        "max": round(max(values), 1) if values else 0.0,
    }


def summarize(rows: list[dict]) -> dict:
    model_rows = [row for row in rows if int(row.get("model_calls") or 0) > 0]
    if not model_rows:
        raise ValueError("results file has no model-backed rows")

    def field(row: dict, key: str) -> float:
        trace = row.get("trace_summary") or {}
        value = trace.get(key, row.get(key))
        return float(value or 0)

    latency = [float(row.get("latency_ms") or 0) for row in model_rows]
    author = [field(row, "author_ms") for row in model_rows]
    review = [field(row, "review_ms") for row in model_rows]
    review_share = [
        field(row, "review_ms") / float(row["latency_ms"])
        for row in model_rows
        if row.get("latency_ms")
    ]
    categories: dict[str, list[dict]] = defaultdict(list)
    for row in model_rows:
        categories[str(row.get("category") or "unknown")].append(row)

    by_category = {}
    for name, items in sorted(categories.items()):
        by_category[name] = {
            "rows": len(items),
            "latency_ms": _stats([float(item.get("latency_ms") or 0) for item in items]),
            "author_ms": _stats([field(item, "author_ms") for item in items]),
            "review_ms": _stats([field(item, "review_ms") for item in items]),
            "model_calls": _stats([float(item.get("model_calls") or 0) for item in items]),
            "review_calls": _stats([field(item, "review_calls") for item in items]),
            "retry_wait_ms": _stats([field(item, "retry_wait_ms") for item in items]),
            "tool_calls": _stats([field(item, "tool_calls") for item in items]),
            "completion_tokens": _stats([float(item.get("completion_tokens") or 0) for item in items]),
        }

    return {
        "schema": "filingcopilot.latency-summary/v1",
        "model_backed_rows": len(model_rows),
        "latency_ms": _stats(latency),
        "author_ms": _stats(author),
        "review_ms": _stats(review),
        "review_share_p50": round(statistics.median(review_share), 4) if review_share else 0.0,
        "model_calls": _stats([float(row.get("model_calls") or 0) for row in model_rows]),
        "review_calls": _stats([field(row, "review_calls") for row in model_rows]),
        "retry_wait_ms": _stats([field(row, "retry_wait_ms") for row in model_rows]),
        "tool_calls": _stats([field(row, "tool_calls") for row in model_rows]),
        "prompt_tokens": _stats([float(row.get("prompt_tokens") or 0) for row in model_rows]),
        "completion_tokens": _stats([float(row.get("completion_tokens") or 0) for row in model_rows]),
        "by_category": by_category,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.results.read_text(encoding="utf-8"))
    summary = summarize(rows)
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
