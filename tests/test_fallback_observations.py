"""Repeated disclosures must not crowd out a readable numeric fallback."""

from copy import deepcopy

import pytest

from filingcopilot.agent_execution import ExecutionMixin


def observed_versions(tools, **changes):
    original = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    other = deepcopy(original)
    other.update(id="second-version", filing_id="AAPL:later", **changes)
    other["filing"] = {**original["filing"], "id": "AAPL:later", "filed": "2026-01-30"}
    tools.ledger[other["id"]] = other
    return original, other


def test_equal_observations_are_shown_once_without_mutating_the_ledger(tools):
    observed_versions(tools)
    before = deepcopy(tools.ledger)
    claims = ExecutionMixin._observed_typed_fallback(tools, "AAPL FY2025 risks?", "AAPL")
    assert len(claims) == 1
    assert claims[0]["evidence_ids"][0] in before
    assert tools.ledger == before
    assert not ExecutionMixin._fallback_covers_question(tools, "AAPL FY2025 risks?", claims, "AAPL")


@pytest.mark.parametrize("changes", [
    {"value": "200.001"},  # Same rounded display, different underlying value.
    {"unit": "EUR"},
    {"metric": "gross_profit"},
    {"concept": "us-gaap:Other"},
    {"start": "2024-10-02"},
    {"end": "2025-09-28"},
    {"fiscal_year": 2024},
])
def test_distinct_observations_are_not_collapsed(tools, changes):
    observed_versions(tools, **changes)
    claims = ExecutionMixin._observed_typed_fallback(tools, "What risks does AAPL disclose?", "AAPL")
    assert len(claims) == 2


@pytest.mark.parametrize("question", [
    "Compare the sources for AAPL FY2025 revenue.",
    "比较 AAPL FY2025 营收的申报版本与重述情况。",
])
def test_source_or_revision_requests_keep_separate_disclosures(tools, question):
    observed_versions(tools)
    claims = ExecutionMixin._observed_typed_fallback(tools, question, "AAPL")
    assert len(claims) == 2
