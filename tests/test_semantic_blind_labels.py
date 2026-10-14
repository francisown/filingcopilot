from copy import deepcopy

import pytest

from scripts.export_semantic_blind_sample import claim_fingerprint, export_claim_sample
from scripts.score_semantic_blind_labels import score


def test_blind_label_score_waits_for_missing_labels():
    report = score(
        [
            {"blind_id": "a", "label": "支持"},
            {"blind_id": "b", "label": None},
        ]
    )

    assert report["status"] == "awaiting_human_labels"
    assert report["labeled_count"] == 1
    assert report["support_rate"] == 1.0
    assert report["unlabeled_ids"] == ["b"]


def test_blind_label_score_accepts_bilingual_labels_and_rejects_unknown():
    report = score(
        [
            {"blind_id": "a", "label": "supported"},
            {"blind_id": "b", "label": "不支持"},
            {"blind_id": "c", "label": "无法判断"},
            {"blind_id": "d", "label": "other"},
        ]
    )

    assert report["status"] == "awaiting_human_labels"
    assert report["label_counts"] == {"support": 1, "unsupported": 1, "unclear": 1}
    assert report["invalid_ids"] == ["d"]


def frozen_run():
    evidence = {"id": "p1", "kind": "passage", "text": "Demand increased.", "verification": "hidden"}
    claims = [
        {"text": "Demand increased", "kind": "narrative", "evidence_ids": ["p1"], "verification": "semantic_review"},
        {"text": "Revenue: 100 USD", "kind": "fact", "evidence_ids": ["f1"], "verification": "deterministic"},
    ]
    return {"answer": claims, "evidence": [evidence, {"id": "f1", "value": "100", "unit": "USD"}]}


def test_claim_export_includes_frozen_evidence_without_reviewer_judgments():
    run = frozen_run()
    before = deepcopy(run)
    rows = [{"id": "q1", "run_id": "run1", "question": "Why?", "answer": "Demand increased\nRevenue: 100 USD"}]
    sample, key = export_claim_sample(rows, lambda _: run, size=20)

    assert len(sample) == 2
    narrative = next(row for row in sample if row["kind"] == "narrative")
    assert narrative["evidence"][0]["text"] == "Demand increased."
    assert "verification" not in narrative["evidence"][0]
    assert "inputs" not in narrative["evidence"][0]
    assert "independent_review_supported" not in narrative
    assert not any("status" in row or "run_id" in row for row in sample)
    assert all(row["label"] is None for row in sample)
    assert key["population"] == {"claims": 2, "independent_reviewed": 1}
    assert run == before
    assert export_claim_sample(rows, lambda _: run, size=20) == (sample, key)


def test_reviewed_only_sample_excludes_deterministic_claims_without_filling_labels():
    run = frozen_run()
    rows = [{"id": "q1", "run_id": "run1", "question": "Why?", "answer": "Demand increased\nRevenue: 100 USD"}]
    sample, key = export_claim_sample(rows, lambda _: run, size=40, reviewed_only=True)
    assert len(sample) == 1 and sample[0]["kind"] == "narrative"
    assert sample[0]["label"] is None
    assert key["population"]["independent_reviewed"] == 1
    assert score(sample, review_key=key)["review_calibration"]["review_miss_rate"] is None


def test_claim_export_skips_empty_missing_or_changed_results_and_retains_missing_evidence():
    run = frozen_run()
    run["evidence"] = []
    rows = [
        {"id": "empty", "answer": ""},
        {"id": "missing", "run_id": "absent", "answer": "text"},
        {"id": "changed", "run_id": "run1", "answer": "changed"},
        {"id": "okay", "run_id": "run1", "answer": "Demand increased\nRevenue: 100 USD"},
    ]
    sample, key = export_claim_sample(rows, lambda identifier: run if identifier == "run1" else None)

    assert len(sample) == 2
    assert key["exclusions"] == {"empty_answer": 1, "missing_frozen_run": 1, "changed_frozen_answer": 1}
    assert all(row["missing_evidence_ids"] and not row["evidence"] for row in sample)


def calibration_rows():
    rows = [
        {"blind_id": "missed", "kind": "narrative", "answer": "a", "label": "不支持"},
        {"blind_id": "caught", "kind": "narrative", "answer": "b", "label": "unsupported"},
        {"blind_id": "supported", "kind": "analysis", "answer": "c", "label": "支持"},
        {"blind_id": "unclear", "kind": "analysis", "answer": "d", "label": "无法判断"},
        {"blind_id": "numeric", "kind": "fact", "answer": "e", "label": "unsupported"},
    ]
    verdicts = [True, False, True, True, None]
    key = {"schema": "filingcopilot.semantic-blind-key/v1", "entries": [
        {"blind_id": row["blind_id"], "claim_sha256": claim_fingerprint(row), "independent_review_supported": verdict}
        for row, verdict in zip(rows, verdicts, strict=True)
    ]}
    return rows, key


def test_miss_rate_uses_unsupported_reviewed_claims_and_excludes_unclear_and_numeric():
    rows, key = calibration_rows()
    report = score(rows, review_key=key, reviewer="reviewer-a")

    assert report["status"] == "scored"
    assert report["human_reviewer"] == "reviewer-a"
    assert report["unit"] == "claim"
    calibration = report["review_calibration"]
    assert calibration["conclusive_reviewed_claims"] == 3
    assert calibration["unsupported_reviewed_claims"] == 2
    assert calibration["missed_unsupported_claims"] == 1
    assert calibration["review_miss_rate"] == 0.5
    assert calibration["approved_claim_error_rate"] == 0.5


def test_miss_rate_stays_null_until_complete_and_denominators_are_nonzero():
    rows, key = calibration_rows()
    rows[0]["label"] = None
    report = score(rows, review_key=key)
    assert report["status"] == "awaiting_human_labels"
    assert report["review_calibration"]["review_miss_rate"] is None
    for row in rows:
        row["label"] = "support"
    report = score(rows, review_key=key)
    assert report["review_calibration"]["review_miss_rate"] is None
    assert report["review_calibration"]["approved_claim_error_rate"] == 0.0
    assert score(rows)["review_calibration"]["status"] == "not_supplied"


def test_changed_claim_and_mismatched_or_invalid_key_prevent_calibration():
    rows, key = calibration_rows()
    rows[0]["answer"] = "changed assertion"
    report = score(rows, review_key=key)
    assert report["status"] == "invalid_calibration_key"
    assert report["review_calibration"]["review_miss_rate"] is None
    assert report["review_calibration"]["errors"]
    key["entries"][0]["blind_id"] = []
    assert score(rows, review_key=key)["status"] == "invalid_calibration_key"


def test_empty_or_duplicate_sample_cannot_report_a_valid_score():
    assert score([])["status"] == "awaiting_human_labels"
    report = score([{"blind_id": "a", "label": "support"}, {"blind_id": "a", "label": "support"}])
    assert report["status"] == "awaiting_human_labels"
    assert report["labeled_count"] == 1
    assert report["invalid_ids"] == ["a"]


@pytest.mark.parametrize("blind_id", [None, 0, True, [], {}, "", "   "])
def test_invalid_worksheet_ids_are_rejected_without_crashing_calibration(blind_id):
    malformed = {"blind_id": blind_id, "kind": "narrative", "answer": "a", "label": "support"}
    report = score([malformed])
    assert report["status"] == "awaiting_human_labels"
    assert report["labeled_count"] == 0
    assert report["support_rate"] is None
    assert report["invalid_ids"]

    rows, key = calibration_rows()
    report = score([*rows, malformed], review_key=key)
    assert report["status"] != "scored"
    assert report["review_calibration"]["review_miss_rate"] is None
    assert report["review_calibration"]["conclusive_reviewed_claims"] == 3


def test_duplicate_ids_do_not_inflate_reviewed_denominators():
    rows, key = calibration_rows()
    report = score([*rows, deepcopy(rows[0])], review_key=key)
    assert report["status"] == "awaiting_human_labels"
    assert report["review_calibration"]["review_miss_rate"] is None
    assert report["review_calibration"]["conclusive_reviewed_claims"] == 3
    assert report["review_calibration"]["unsupported_reviewed_claims"] == 2


def test_frozen_key_declared_sample_size_must_match_worksheet():
    rows, key = calibration_rows()
    key["sample_size"] = len(rows) + 1
    report = score(rows, review_key=key)
    assert report["status"] == "invalid_calibration_key"
    assert report["review_calibration"]["review_miss_rate"] is None


@pytest.mark.parametrize("kind", ["fact", "calculation", "excerpt"])
def test_deterministic_claims_cannot_enter_semantic_review_denominators(kind):
    rows, key = calibration_rows()
    rows[0]["kind"] = kind
    key["entries"][0]["claim_sha256"] = claim_fingerprint(rows[0])
    report = score(rows, review_key=key)
    assert report["status"] == "invalid_calibration_key"
    assert report["review_calibration"]["review_miss_rate"] is None
    assert report["review_calibration"]["conclusive_reviewed_claims"] == 2


def test_semantic_marker_on_a_fact_does_not_turn_it_into_reviewed_prose():
    run = frozen_run()
    run["answer"][1]["verification"] = "semantic_review"
    rows = [{"id": "q1", "run_id": "run1", "question": "Why?", "answer": "Demand increased\nRevenue: 100 USD"}]
    sample, key = export_claim_sample(rows, lambda _: run)
    assert key["population"]["independent_reviewed"] == 1
    fact_id = next(row["blind_id"] for row in sample if row["kind"] == "fact")
    assert next(entry for entry in key["entries"] if entry["blind_id"] == fact_id)["independent_review_supported"] is None
