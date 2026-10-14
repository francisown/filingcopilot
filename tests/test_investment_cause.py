import pytest

from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission

CASH_SOURCE = (
    "Cash provided by investing activities varies with purchases, sales and maturities "
    "of marketable securities. Cash payments for acquisitions and investments in other "
    "companies increased. We invested in preferred stock and convertible notes."
)


def check_statement(tools, statement, source):
    # Real verification boundary; the independent reviewer is deliberately overconfident.
    tools.search_passages("manufacturing partners", ticker="AAPL", lexical_only=True)
    tools.ledger["p1"]["text"] = source
    reviewed = []

    def review(question, claims):
        reviewed.extend(claims)
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "Controlled approval"}
                for c in claims
            ]
        }

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=statement, evidence_ids=["p1"])]),
        tools,
        "股票怎么样？",
        semantic_review=review,
        focus_ticker="AAPL",
    )
    return accepted, errors, reviewed


@pytest.mark.parametrize(
    "statement",
    [
        "净利润增长可能受非经常投资收益影响。",
        "投资收益推动了盈利增长。",
        "Net income growth may be driven by investment gains.",
        "净利润可能受投资收益影响，仍需核验其他因素。",
    ],
)
def test_cash_investment_activity_cannot_prove_a_profit_driver_even_with_hedging(tools, statement):
    accepted, errors, reviewed = check_statement(tools, statement, CASH_SOURCE)
    assert not accepted and not reviewed
    assert any("Investment cash" in error for error in errors)


@pytest.mark.parametrize(
    "source",
    [
        CASH_SOURCE + " Net income includes unrealized gains on our equity investments.",
        "Investment income contributed to net income growth.",
        "净利润增长主要来自投资收益。",
    ],
)
def test_additional_earnings_source_still_requires_semantic_review(tools, source):
    accepted, errors, reviewed = check_statement(tools, "净利润增长可能受投资收益影响。", source)
    assert accepted and reviewed and not errors


@pytest.mark.parametrize(
    "statement",
    [
        "投资收益是否影响净利润增长仍需核验。",
        "尚未核验投资收益对净利润的影响。",
        "投资现金支出不证明投资收益影响净利润。",
        "现金投资支出增加，投资回报仍需后续核验。",
    ],
)
def test_open_questions_negations_and_cash_tradeoffs_are_not_profit_cause_assertions(
    tools, statement
):
    accepted, errors, reviewed = check_statement(tools, statement, CASH_SOURCE)
    assert accepted and reviewed and not errors


def test_unrecognized_source_is_left_to_independent_review(tools):
    accepted, errors, reviewed = check_statement(
        tools, "净利润增长可能受投资收益影响。", "Competition could affect future operations."
    )
    assert accepted and reviewed and not errors
