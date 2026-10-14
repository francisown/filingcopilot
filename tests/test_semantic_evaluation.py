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
