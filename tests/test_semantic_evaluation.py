import json
from pathlib import Path

from scripts.evaluate_semantic_set import evaluate


def _gold():
    return json.loads(
        (Path(__file__).parents[1] / "evaluation" / "semantic_questions_v1.json").read_text(encoding="utf-8")
    )


def test_semantic_gold_covers_seven_categories_and_five_companies():
    gold = _gold()
    assert gold["count"] == len(gold["items"]) == 140
    assert set(gold["categories"]) == {
        "numeric", "causality", "negation", "risk", "cross_period", "insufficient_evidence", "market_outage"
    }
    assert {item["ticker"] for item in gold["items"]} >= {"AAPL", "AMZN", "MSFT", "NVDA", "TSM"}
    assert all(sum(item["category"] == category for item in gold["items"]) >= 20 for category in gold["categories"])


def test_semantic_scoring_does_not_credit_short_answers_or_missing_citations():
    gold = _gold()
    results = [
        {"id": gold["items"][0]["id"], "status": "answered", "answer": "too short", "has_citation": True,
         "citation_supported": True, "latency_ms": 10, "cost_usd": 0.01},
        {"id": gold["items"][1]["id"], "status": "answered", "answer": "A" * 80, "has_citation": True,
         "citation_supported": False, "unsupported_claims": 1, "numeric_errors": 1, "latency_ms": 20, "cost_usd": 0.02},
        {"id": gold["items"][2]["id"], "status": "abstained", "answer": "证据不足", "reasonable_refusal": True,
         "latency_ms": 30, "cost_usd": 0.0},
    ]
    report = evaluate(gold, results)
    assert report["short_answer_excluded"] == [gold["items"][0]["id"]]
    assert report["eligible_results"] == 2
    assert report["citation_support_rate"] == 0.0
    assert report["reasonable_refusal_rate"] == 1.0
    assert report["refusal_quality_denominator"] == 1
    assert report["human_semantic_citation_rate"] is None
    assert report["p50_latency_ms"] == 25.0
    assert report["model_backed_questions"] == 0
    assert report["model_backed_p50_latency_ms"] is None


def test_timeouts_do_not_dilute_refusal_quality_denominator():
    gold = _gold()
    results = [
        {"id": gold["items"][0]["id"], "status": "abstained", "budget_timeout": True,
         "wrong_refusal": True, "reasonable_refusal": True},
        {"id": gold["items"][1]["id"], "status": "abstained", "budget_timeout": False,
         "wrong_refusal": True, "reasonable_refusal": False},
    ]
    report = evaluate(gold, results)
    assert report["timeout_row_count"] == 1
    assert report["refusal_quality_denominator"] == 1
    assert report["wrong_refusal_rate"] == 1.0
    assert report["reasonable_refusal_rate"] == 0.0


def test_scoring_separates_answerable_questions_from_intended_refusals():
    gold = {
        "items": [
            {"id": "answer", "expected_behavior": "cite_risk_evidence_and_keep_scope"},
            {"id": "refuse", "expected_behavior": "reasonable_refusal_with_actionable_gap"},
            {"id": "policy", "expected_behavior": "partial_answer_with_source_failure_and_next_step"},
        ]
    }
    report = evaluate(
        gold,
        [
            {"id": "answer", "status": "partial", "answer": "evidence", "latency_ms": 1},
            {
                "id": "refuse",
                "status": "abstained",
                "reasonable_refusal": True,
                "latency_ms": 1,
            },
            {"id": "policy", "status": "partial", "answer": "source gap", "latency_ms": 1},
        ],
    )
    assert report["coverage_rate"] == 0.0
    assert report["answerable_questions"] == 1
    assert report["answerable_coverage_rate"] == 0.0
    assert report["expected_refusal_questions"] == 1
    assert report["expected_refusal_rate"] == 1.0
    assert report["policy_case_questions"] == 1
    assert report["policy_case_successes"] == 1
    assert report["policy_case_success_rate"] == 1.0


def test_empty_policy_partial_is_not_counted_as_success():
    gold = {"items": [{"id": "policy", "expected_behavior": "partial_answer_with_source_failure_and_next_step"}]}
    report = evaluate(gold, [{"id": "policy", "status": "partial", "answer": "", "answer_claims": 0}])
    assert report["policy_case_questions"] == 1
    assert report["policy_case_successes"] == 0
    assert report["policy_case_success_rate"] == 0.0


def test_latency_reports_model_backed_subset_separately():
    gold = {
        "items": [
            {"id": "typed", "expected_behavior": "answer_with_typed_fact_or_calculation"},
            {"id": "narrative", "expected_behavior": "cite_risk_evidence_and_keep_scope"},
        ]
    }
    report = evaluate(gold, [
        {"id": "typed", "status": "answered", "answer": "x" * 40, "model_calls": 0, "latency_ms": 20},
        {"id": "narrative", "status": "partial", "answer": "y" * 40, "model_calls": 2, "latency_ms": 100},
    ])
    assert report["p50_latency_ms"] == 60.0
    assert report["model_backed_questions"] == 1
    assert report["model_backed_p50_latency_ms"] == 100.0
    assert report["model_backed_p95_latency_ms"] == 100.0


def test_intended_abstention_does_not_dilute_citation_binding():
    """A correct refusal delivers no claim, so it cannot be a citation failure."""
    gold = {
        "items": [
            {"id": "cited-a", "expected_behavior": "cite_risk_evidence_and_keep_scope"},
            {"id": "cited-b", "expected_behavior": "cite_risk_evidence_and_keep_scope"},
            {"id": "refused", "expected_behavior": "reasonable_refusal_with_actionable_gap"},
        ]
    }
    report = evaluate(
        gold,
        [
            {"id": "cited-a", "status": "answered", "answer": "a" * 80, "has_citation": True,
             "citation_supported": True, "answer_claims": 4, "latency_ms": 1},
            {"id": "cited-b", "status": "partial", "answer": "b" * 80, "has_citation": True,
             "citation_supported": True, "answer_claims": 2, "latency_ms": 1},
            {"id": "refused", "status": "abstained", "answer": "证据不足", "has_citation": False,
             "reasonable_refusal": True, "answer_claims": 0, "latency_ms": 1},
        ],
    )
    assert report["delivered_answer_rows"] == 2
    assert report["citation_denominator"] == 2
    assert report["citation_binding_rate"] == 1.0
    # The retired all-rows denominator is still published, under an explicit name,
    # so the old figure can be explained rather than silently rewritten.
    assert report["citation_binding_rate_all_rows"] == 0.666667
    assert report["claim_denominator"] == 6


def test_claim_rates_use_a_claim_denominator_and_cannot_exceed_one():
    gold = {"items": [{"id": "q", "expected_behavior": "cite_risk_evidence_and_keep_scope"}]}
    report = evaluate(
        gold,
        [
            {
                "id": "q",
                "status": "answered",
                "answer": "x" * 80,
                "has_citation": True,
                "citation_supported": True,
                "answer_claims": 4,
                "unsupported_claims": 2,
                "numeric_errors": 1,
            }
        ],
    )
    # Two unsupported claims out of four claims is a proportion, not "2.0".
    assert report["unsupported_claims"] == 2
    assert report["claim_denominator"] == 4
    assert report["unsupported_claim_rate"] == 0.5
    assert report["numeric_error_rate"] == 0.25
    # The per-row mean is kept only under a name that states its unit.
    assert report["unsupported_claims_per_answered_row"] == 2.0
