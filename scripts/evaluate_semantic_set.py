"""Score captured answer records without treating abstention or truncation as accuracy."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def evaluate(gold: dict, results: list[dict] | None) -> dict:
    items = {item["id"]: item for item in gold["items"]}
    rows = results or []
    by_id = {row.get("id"): row for row in rows if row.get("id") in items}
    eligible = []
    excluded_short = []
    for item_id, row in by_id.items():
        minimum = int(items[item_id].get("minimum_answer_chars", 40))
        if row.get("status") == "answered" and len(str(row.get("answer", ""))) < minimum:
            excluded_short.append(item_id)
            continue
        eligible.append((items[item_id], row))
    answered = sum(row.get("status") == "answered" for _, row in eligible)
    timeout_rows = sum(bool(row.get("budget_timeout")) for _, row in eligible)
    refusal_rows = [
        row
        for _, row in eligible
        if row.get("status") in ("refused", "abstained") and not row.get("budget_timeout")
    ]
    reasonable_refusal = sum(bool(row.get("reasonable_refusal")) for row in refusal_rows)
    unsupported = sum(int(row.get("unsupported_claims", 0)) for _, row in eligible)
    numeric_errors = sum(int(row.get("numeric_errors", 0)) for _, row in eligible)
    wrong_refusal = sum(bool(row.get("wrong_refusal")) for row in refusal_rows)
    runner_errors = sum(row.get("status") == "runner_error" for _, row in eligible)
    citation_rows = [row for _, row in eligible if row.get("has_citation") is not None]
    citation_supported = sum(bool(row.get("citation_supported")) for row in citation_rows)
    latencies = [float(row["latency_ms"]) for _, row in eligible if row.get("latency_ms") is not None]
    costs = [float(row["cost_usd"]) for _, row in eligible if row.get("cost_usd") is not None]
    latencies.sort()
    return {
        "gold_questions": len(items),
        "captured_results": len(by_id),
        "eligible_results": len(eligible),
        "short_answer_excluded": excluded_short,
        "coverage_rate": _ratio(answered, len(eligible)),
        "unsupported_claim_rate": _ratio(unsupported, max(answered, 1)),
        "numeric_error_rate": _ratio(numeric_errors, max(answered, 1)),
        "wrong_refusal_rate": _ratio(wrong_refusal, len(refusal_rows)),
        "reasonable_refusal_rate": _ratio(reasonable_refusal, len(refusal_rows)),
        "refusal_quality_denominator": len(refusal_rows),
        "timeout_row_count": timeout_rows,
        "runner_error_count": runner_errors,
        "citation_support_rate": _ratio(citation_supported, len(citation_rows)),
        "citation_binding_rate": _ratio(citation_supported, len(citation_rows)),
        "human_semantic_citation_rate": None,
        "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
        "p95_latency_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3)
        if latencies else None,
        "mean_cost_usd": round(statistics.mean(costs), 8) if costs else None,
        "cost_observations": len(costs),
        "answer_shortening_is_not_credit": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("evaluation/semantic_questions_v1.json"))
    parser.add_argument("--results", type=Path, help="Captured JSON list; omit for a schema-only report")
    parser.add_argument("--output", type=Path, default=Path("artifacts/semantic-evaluation.json"))
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8")) if args.results else None
    report = {
        "schema": "filingcopilot.semantic-evaluation/v1",
        "gold": {"path": str(args.gold), "version": gold.get("version"), "count": len(gold["items"])},
        "status": "scored" if results is not None else "not_run",
        "metrics": evaluate(gold, results),
        "notes": [
            "Coverage and citation metrics count only captured, non-truncated answers.",
            "Shortening an answer below the declared minimum is excluded rather than rewarded.",
            "A review model must be configured independently; this report does not infer factual truth from a model self-score.",
            "citation_support_rate is deterministic evidence-ID binding; human_semantic_citation_rate stays null until blind labels exist.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
