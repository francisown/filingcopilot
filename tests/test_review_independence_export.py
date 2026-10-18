from copy import deepcopy

import pytest

from scripts.export_semantic_blind_sample import export_claim_sample


def run_with_review():
    return {
        "answer": [{"index": 0, "text": "Demand increased.", "kind": "narrative",
                    "evidence_ids": ["p1"], "verification": "semantic_review"}],
        "evidence": [{"id": "p1", "kind": "passage", "text": "Demand increased."}],
        "trace": [{"stage": "reason", "model": "author"},
                  {"stage": "semantic_review", "model": "reviewer"}],
    }


def exported(run, *, reviewed_only=True):
    return export_claim_sample(
        [{"id": "q1", "question": "Why?", "run_id": "r1", "answer": "Demand increased."}],
        lambda _: run, reviewed_only=reviewed_only,
    )


@pytest.mark.parametrize("failure", ["same_author", "fallback_author", "not_independent", "unknown", "no_trace", "missing_author", "missing_reviewer"])
def test_unproven_or_self_review_is_never_counted_as_independent(failure):
    run = run_with_review()
    if failure == "same_author":
        run["trace"][1]["model"] = "author"
    elif failure == "fallback_author":
        run["trace"].append({"stage": "reason", "model": "reviewer"})
    elif failure in ("not_independent", "unknown"):
        run["trace"][1]["review_independence"] = failure
    elif failure == "no_trace":
        run["trace"] = []
    elif failure == "missing_author":
        run["trace"][0].pop("model")
    else:
        run["trace"][1].pop("model")
    before = deepcopy(run)
    sample, key = exported(run)
    assert sample == []
    assert key["population"]["independent_reviewed"] == 0
    # The general worksheet can still ask humans to assess this answer, but
    # its private key cannot imply an independent model approval.
    sample, key = exported(run, reviewed_only=False)
    assert len(sample) == 1 and sample[0]["label"] is None
    assert key["entries"][0]["independent_review_supported"] is None
    assert "review_independence" not in sample[0]
    assert run == before


def test_separate_recorded_models_remain_eligible_without_filling_human_labels():
    sample, key = exported(run_with_review())
    assert len(sample) == 1 and sample[0]["label"] is None
    assert key["entries"][0]["independent_review_supported"] is True
