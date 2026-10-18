"""Summarize paired run receipts without converting self-checks into accuracy."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def claim_kinds(directory: Path, question_id: str):
    path = directory / (question_id + ".answer.json")
    if not path.exists():
        return []
    return sorted({claim["kind"] for claim in read(path).get("answer", [])})


def summarize(directory: Path):
    rows = read(directory / "results.json")
    latencies = sorted(row["latency_ms"] / 1000 for row in rows)
    receipts = [read(path) for path in sorted(directory.glob("*.answer.json"))]
    tokens = Counter()
    independence = Counter()
    content = Counter()
    for receipt in receipts:
        kinds = {claim["kind"] for claim in receipt.get("answer", [])}
        content[
            "contains_narrative_or_analysis"
            if kinds & {"narrative", "analysis"}
            else "typed_or_market_only"
            if kinds
            else "empty"
        ] += 1
        usage = receipt.get("usage", {})
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            tokens[key] += usage.get(key, 0)
        labels = {
            event["review_independence"]
            for event in receipt.get("trace", [])
            if event.get("review_independence")
        }
        independence.update(labels)
    return {
        "completed": (directory / "complete.json").exists(),
        "questions": len(rows),
        "status": dict(Counter(row["status"] for row in rows)),
        "nonempty_answers": sum(bool(row.get("answer", "").strip()) for row in rows),
        "budget_timeouts": sum(bool(row.get("budget_timeout")) for row in rows),
        "exception_types": dict(
            Counter(row["error_type"] for row in rows if row.get("error_type"))
        ),
        "http_requests": sum(row.get("http_requests", 0) for row in rows),
        "http_responses": dict(
            Counter(str(s) for row in rows for s in row.get("http_statuses", []))
        ),
        "latency_seconds": {
            "median": statistics.median(latencies) if latencies else None,
            "p95_nearest_rank": latencies[math.ceil(len(latencies) * 0.95) - 1]
            if latencies
            else None,
            "sum_query_times": round(sum(latencies), 3),
        },
        "reported_tokens": dict(tokens),
        "answer_receipts": len(receipts),
        "receipt_content_types": dict(content),
        "questions_with_review_independence_label": dict(independence),
        "categories": {
            category: {
                "status": dict(
                    Counter(row["status"] for row in rows if row["category"] == category)
                ),
                "nonempty_answers": sum(
                    bool(row.get("answer", "").strip())
                    for row in rows
                    if row["category"] == category
                ),
            }
            for category in sorted({row["category"] for row in rows})
        },
    }


def compare(baseline: Path, candidate: Path):
    before = {row["id"]: row for row in read(baseline / "results.json")}
    after = {row["id"]: row for row in read(candidate / "results.json")}
    common = sorted(before.keys() & after.keys())
    transitions = Counter(f"{before[key]['status']} -> {after[key]['status']}" for key in common)
    changes = []
    for key in common:
        a, b = before[key], after[key]
        if a["status"] != b["status"] or bool(a.get("answer")) != bool(b.get("answer")):
            changes.append(
                {
                    "id": key,
                    "category": a["category"],
                    "baseline": a["status"],
                    "candidate": b["status"],
                    "baseline_nonempty": bool(a.get("answer", "").strip()),
                    "candidate_nonempty": bool(b.get("answer", "").strip()),
                    "baseline_claim_kinds": claim_kinds(baseline, key),
                    "candidate_claim_kinds": claim_kinds(candidate, key),
                    "baseline_latency_seconds": a["latency_ms"] / 1000,
                    "candidate_latency_seconds": b["latency_ms"] / 1000,
                }
            )
    return {
        "baseline": summarize(baseline),
        "candidate": summarize(candidate),
        "paired_questions": len(common),
        "transitions": dict(transitions),
        "changed_status_or_delivery": changes,
        "limitations": [
            "Status and evidence-binding self-checks are not independent semantic accuracy labels.",
            "Single model sample per arm per question; provider latency and model randomness remain.",
            "Reported tokens omit unreported usage on failed requests and are not an invoice.",
            "Review independence labels are absent in GitHub; absence does not establish independence.",
            "Per-query latency includes queueing within a query but not waiting for a worker.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.baseline, args.candidate)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {name: report[name] for name in ("paired_questions", "transitions")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
