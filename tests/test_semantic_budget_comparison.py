from copy import deepcopy

import pytest

from scripts.compare_semantic_budgets import _gold_hash, compare


def _inputs():
    item = {"id": "q1", "ticker": "AAPL", "category": "risk", "question": "Risk?",
            "expected_behavior": "answer_with_passage", "minimum_answer_chars": 40}
    gold = {"items": [item]}
    rows = [{**item, "status": "answered", "answer": "Short", "evaluation_fingerprint": "frozen"}]
    report = {"status": "scored", "run_configuration": {"research_timeout_seconds": 240, "model_timeout_seconds": 60},
              "research_model": "research", "review_model": "review",
              "run_context": {"gold_sha256": _gold_hash(gold), "fingerprint": "frozen"}}
    return gold, rows, report


def test_short_answers_do_not_reduce_complete_set_denominator():
    gold, rows, report = _inputs()
    production = deepcopy(rows)
    production[0]["answer"] = "Long " * 15
    result = compare(gold, rows, report, production, report)
    assert result["tight_budget"]["coverage_all_questions"] == 0
    assert result["production_budget"]["coverage_all_questions"] == 1
    assert result["differences"]["adequate_answer_count"] == 1


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_question", "budget", "review_model", "fingerprint", "gold"])
def test_incomplete_or_mixed_production_baseline_is_not_published(mutation):
    gold, rows, report = _inputs()
    production = deepcopy(rows)
    if mutation == "missing":
        production.clear()
    elif mutation == "duplicate":
        production.append(deepcopy(production[0]))
    elif mutation == "wrong_question":
        production[0]["question"] = "Other question"
    elif mutation == "budget":
        report["run_configuration"]["research_timeout_seconds"] = 20
    elif mutation == "review_model":
        report["review_model"] = report["research_model"]
    elif mutation == "fingerprint":
        production[0]["evaluation_fingerprint"] = "other"
    else:
        report["run_context"]["gold_sha256"] = "other"
    with pytest.raises(ValueError):
        compare(gold, rows, report, production, report)
