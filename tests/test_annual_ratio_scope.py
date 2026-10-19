"""Prior-year ratios are valid premises only for requested annual comparisons."""

from copy import deepcopy

import pytest

from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission


def margins(tools):
    revenue = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    prior_revenue = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    gross = tools.get_financial_fact("AAPL", "gross_profit", 2025)["evidence"]
    prior_gross = deepcopy(gross)
    prior_gross.update(id="gross24", value="30", **{
        key: prior_revenue[key] for key in ("fiscal_year", "start", "end")
    })
    tools.ledger[prior_gross["id"]] = prior_gross
    current = tools.calculate("ratio", gross["id"], revenue["id"])["evidence"]
    prior = tools.calculate("ratio", prior_gross["id"], prior_revenue["id"])["evidence"]
    return [Claim(kind="calculation", evidence_ids=[item["id"]]) for item in (current, prior)]


@pytest.mark.parametrize("question", [
    "Compare Apple's FY2025 gross margin with the prior year.",
    "苹果 FY2025 毛利率变化由哪些已披露因素解释？",
    "苹果的 FY2025 利润率变化由哪些已披露因素解释？",
])
def test_prior_annual_margin_is_in_scope_for_comparison(tools, question):
    accepted, errors = verify_submission(
        Submission(claims=margins(tools), gaps=["Explanation is reviewed separately."]), tools, question,
    )
    assert len(accepted) == 2 and not errors


@pytest.mark.parametrize("question", [
    "What was Apple's FY2025 gross margin?",
    "Compare Apple's FY2025 and FY2023 gross margins.",
])
def test_comparison_does_not_admit_an_unrequested_ratio_year(tools, question):
    _, prior = margins(tools)
    accepted, errors = verify_submission(Submission(claims=[prior]), tools, question)
    assert not accepted and any("fiscal year" in error for error in errors)


def test_prior_margin_cannot_replace_current_margin(tools):
    _, prior = margins(tools)
    accepted, errors = verify_submission(
        Submission(claims=[prior]), tools,
        "Compare Apple's FY2025 gross margin with the prior year.",
    )
    assert accepted and any("Requested margin" in error for error in errors)
