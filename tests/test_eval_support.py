from __future__ import annotations

from filingcopilot.eval_support import classify_support


def test_full_requires_every_number_unit_and_period() -> None:
    report = classify_support(
        ["FY2023 revenue was 123 million and margin 45%."],
        "FY2023 revenue was 123 million and margin 45%.",
    )
    assert report.status == "full"
    assert not report.missing_numbers
    assert not report.missing_units
    assert not report.missing_periods


def test_one_matching_number_is_only_partial() -> None:
    report = classify_support(
        ["FY2023 revenue was 123 million."],
        "FY2023 revenue was 123 million and margin 45%.",
    )
    assert report.status == "partial"
    assert {str(value) for value in report.missing_numbers} == {"45"}


def test_numeric_substring_is_not_support() -> None:
    report = classify_support(["Revenue was 1234 million."], "123")
    assert report.status == "unsupported"
    assert report.missing_numbers


def test_calculation_can_use_inputs_without_literal_result() -> None:
    report = classify_support(
        ["Revenue was 539.2 million and 427.7 million."],
        "Revenue increased by 111.5 million, calculated as 539.2 minus 427.7.",
    )
    assert report.status == "full"
    assert 111.5 not in report.missing_numbers
    assert report.calculation_supported


def test_invalid_calculation_is_not_full() -> None:
    report = classify_support(
        ["Revenue was 539.2 million and 427.7 million."],
        "The difference was 999 million: 539.2 - 427.7 = 999.",
    )
    assert report.status != "full"
    assert not report.calculation_supported


def test_missing_period_or_unit_prevents_full() -> None:
    no_period = classify_support(["Revenue was 123 million."], "FY2023 revenue was 123 million.")
    assert no_period.status == "partial"
    assert no_period.missing_periods
    no_unit = classify_support(["Revenue was 123."], "Revenue was 123 million.")
    assert no_unit.status == "partial"
    assert no_unit.missing_units


def test_multi_reference_inputs_can_support_calculation() -> None:
    report = classify_support(
        ["Revenue was 539.2 million.", "Prior revenue was 427.7 million."],
        "Revenue increased by 111.5 million, calculated as 539.2 minus 427.7.",
    )
    assert report.status == "full"
    assert report.multi_reference_support
    assert report.calculation_required
    assert report.as_dict()["layers"]["evidence_retrieval"] == "found"
    assert report.as_dict()["layers"]["input_coverage"] == "full"
    assert report.as_dict()["layers"]["answer_proof"] == "proved"


def test_evidence_found_but_incomplete_inputs_is_partial() -> None:
    report = classify_support(
        ["Revenue was 539.2 million."],
        "Revenue increased by 111.5 million, calculated as 539.2 minus 427.7.",
    )
    assert report.status == "partial"
    assert report.as_dict()["layers"]["evidence_retrieval"] == "found"
    assert report.as_dict()["layers"]["input_coverage"] == "partial"
    assert report.as_dict()["layers"]["answer_proof"] == "partial"
