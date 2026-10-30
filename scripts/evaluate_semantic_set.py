"""Score captured answer records without treating abstention or truncation as accuracy."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ANSWERABLE_BEHAVIORS = {
    "answer_with_typed_fact_or_calculation",
    "cite_narrative_evidence_and_avoid_unproven_cause",
    "preserve_negation_and_require_exact_support",
    "cite_risk_evidence_and_keep_scope",
    "same_period_comparison_or_explicit_gap",
}
REFUSAL_BEHAVIOR = "reasonable_refusal_with_actionable_gap"
POLICY_BEHAVIOR = "partial_answer_with_source_failure_and_next_step"


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
    answerable = [
        (item, row)
        for item, row in eligible
        if item.get("expected_behavior") in ANSWERABLE_BEHAVIORS
    ]
    expected_refusals = [
        (item, row)
        for item, row in eligible
        if item.get("expected_behavior") == REFUSAL_BEHAVIOR
    ]
    policy_cases = [
        (item, row)
        for item, row in eligible
        if item.get("expected_behavior") == POLICY_BEHAVIOR
    ]
    policy_delivered = sum(
        bool(row.get("answer_claims") or row.get("answer"))
        and row.get("status") in ("answered", "partial")
        for _, row in policy_cases
    )
    answerable_answered = sum(row.get("status") == "answered" for _, row in answerable)
    correct_refusals = sum(
        row.get("status") in ("abstained", "refused") and bool(row.get("reasonable_refusal"))
        for _, row in expected_refusals
    )
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
    # Citation binding is a property of a delivered answer. A correct abstention
    # delivers no claim, so putting it in the denominator would score an intended
    # refusal as a citation failure. The all-rows figure is kept, explicitly
    # named, only so historical reports stay comparable.
    delivered_rows = [row for _, row in eligible if row.get("has_citation")]
    citation_supported = sum(bool(row.get("citation_supported")) for row in delivered_rows)
    total_claims = sum(int(row.get("answer_claims") or 0) for _, row in eligible)
    latencies = [float(row["latency_ms"]) for _, row in eligible if row.get("latency_ms") is not None]
    model_latencies = [
        float(row["latency_ms"])
        for _, row in eligible
        if row.get("latency_ms") is not None and int(row.get("model_calls") or 0) > 0
    ]
    costs = [float(row["cost_usd"]) for _, row in eligible if row.get("cost_usd") is not None]
    # Prices are optional; token counts are always recorded, so cost can at least
    # be observed as usage even when no per-million price is configured.
    prompt_tokens = [int(row["prompt_tokens"]) for _, row in eligible if row.get("prompt_tokens") is not None]
    completion_tokens = [int(row["completion_tokens"]) for _, row in eligible if row.get("completion_tokens") is not None]
    latencies.sort()
    model_latencies.sort()
    return {
        "gold_questions": len(items),
        "captured_results": len(by_id),
        "eligible_results": len(eligible),
        "short_answer_excluded": excluded_short,
        "coverage_rate": _ratio(answered, len(eligible)),
        "answerable_questions": len(answerable),
        "answerable_answered": answerable_answered,
        "answerable_coverage_rate": _ratio(answerable_answered, len(answerable)),
        "expected_refusal_questions": len(expected_refusals),
        "correct_expected_refusals": correct_refusals,
        "expected_refusal_rate": _ratio(correct_refusals, len(expected_refusals)),
        "policy_case_questions": len(policy_cases),
        # Policy cases are deliberately excluded from answerable coverage, but
        # they still need an actionable degraded response. Keep this separate
        # so an empty partial result cannot hide behind the denominator rule.
        "policy_case_successes": policy_delivered,
        "policy_case_success_rate": _ratio(policy_delivered, len(policy_cases)),
        # Raw claim counts first: a rate without its denominator is what allowed
        # a per-row mean to be reported as a percentage.
        "unsupported_claims": unsupported,
        "numeric_errors": numeric_errors,
        "claim_denominator": total_claims,
        "unsupported_claim_rate": _ratio(unsupported, total_claims) if total_claims else None,
        "numeric_error_rate": _ratio(numeric_errors, total_claims) if total_claims else None,
        "unsupported_claims_per_answered_row": _ratio(unsupported, max(answered, 1)),
        "numeric_errors_per_answered_row": _ratio(numeric_errors, max(answered, 1)),
        "wrong_refusal_rate": _ratio(wrong_refusal, len(refusal_rows)),
        "reasonable_refusal_rate": _ratio(reasonable_refusal, len(refusal_rows)),
        "refusal_quality_denominator": len(refusal_rows),
        "timeout_row_count": timeout_rows,
        "runner_error_count": runner_errors,
        "delivered_answer_rows": len(delivered_rows),
        "citation_denominator": len(delivered_rows),
        "citation_support_rate": _ratio(citation_supported, len(delivered_rows))
        if delivered_rows else None,
        "citation_binding_rate": _ratio(citation_supported, len(delivered_rows))
        if delivered_rows else None,
        "citation_binding_rate_all_rows": _ratio(citation_supported, len(eligible)),
        "human_semantic_citation_rate": None,
        "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
        "p95_latency_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3)
        if latencies else None,
        "model_backed_questions": len(model_latencies),
        "model_backed_p50_latency_ms": round(statistics.median(model_latencies), 3)
        if model_latencies else None,
        "model_backed_p95_latency_ms": round(
            model_latencies[min(len(model_latencies) - 1, int(len(model_latencies) * 0.95))], 3
        ) if model_latencies else None,
        "mean_cost_usd": round(statistics.mean(costs), 8) if costs else None,
        "cost_observations": len(costs),
        "mean_prompt_tokens": round(statistics.mean(prompt_tokens), 1) if prompt_tokens else None,
        "mean_completion_tokens": round(statistics.mean(completion_tokens), 1)
        if completion_tokens else None,
        "total_tokens": (sum(prompt_tokens) + sum(completion_tokens))
        if prompt_tokens or completion_tokens else None,
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
            "citation_binding_rate and citation_support_rate use delivered answers as the denominator, so an intended abstention is not scored as a citation failure; citation_binding_rate_all_rows keeps the older all-row denominator for comparison only.",
            "answerable_coverage_rate excludes intentional refusal and source-outage policy cases; expected_refusal_rate scores only questions whose correct behavior is to refuse with an actionable evidence gap.",
            "policy_case_success_rate separately checks that source-outage cases deliver an actionable degraded response; they are not allowed to disappear behind the answerable denominator.",
            "Shortening an answer below the declared minimum is excluded rather than rewarded.",
            "A review model must be configured independently; this report does not infer factual truth from a model self-score.",
            "citation_support_rate is deterministic evidence-ID binding; human_semantic_citation_rate stays null until blind labels exist.",
            "Overall latency includes deterministic rows; model_backed_* latency isolates rows that actually made model calls.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
