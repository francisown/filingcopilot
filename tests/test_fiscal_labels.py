"""Reproduce label failures from the captured 0.19 stock research submissions."""

from datetime import date

import pytest

from filingcopilot.models import Claim, Fact, Filing, Passage, Submission
from filingcopilot.verification import verify_submission


def approve(question, claims):
    return {
        "verdicts": [
            {"index": c["index"], "supported": True, "reason": "Controlled reviewer"}
            for c in claims
        ]
    }


def quarter_sources(tools, fiscal=False):
    filing = Filing(
        id="AAPL:quarter-label",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="quarter-label",
        form="10-Q",
        filed="2026-07-31",
        period_end="2026-06-27" if fiscal else "2026-06-30",
        source_url="https://www.sec.gov/Archives/quarter-label.htm",
        content_sha256="c" * 64,
    )
    facts = [
        Fact(
            id=f"label-net-{year}",
            filing_id=filing.id,
            ticker="AAPL",
            metric="net_income",
            label="净利润",
            concept="us-gaap:NetIncomeLoss",
            value="60" if year == 2026 else "20",
            unit="USD",
            fiscal_year=year,
            start=f"{year}-03-29" if fiscal else f"{year}-04-01",
            end=f"{year}-06-27" if fiscal else f"{year}-06-30",
            source_url=filing.source_url,
            locator=f"synthetic {year} quarter record",
        )
        for year in (2026, 2025)
    ]
    passage = Passage(
        id="label-quarter-passage",
        filing_id=filing.id,
        ticker="AAPL",
        section="Management discussion",
        text=(
            "Net income increased in the third quarter of fiscal 2026 ended June 27, 2026."
            if fiscal
            else "Net income increased in the second quarter of 2026 ended June 30, 2026."
        ),
        source_url=filing.source_url,
        locator="synthetic quarter paragraph",
    )
    tools.corpus.ingest(filing, facts, [passage])
    for item in [*facts, passage]:
        tools.ledger[item.id] = tools.corpus.evidence(item.id, tools.as_of)
    growth = tools.calculate("growth", facts[0].id, facts[1].id)["evidence"]
    return [growth["id"], passage.id]


def test_cited_quarter_in_year_after_order_is_a_date_not_an_amount(tools):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="Q2 2026净利润同比改善，尚不足以证明长期加速。",
                    evidence_ids=ids,
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


def test_calendar_quarter_cannot_replace_source_disclosed_fiscal_quarter(tools):
    ids = quarter_sources(tools, fiscal=True)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="FY2026Q2净利润同比改善。",
                    evidence_ids=ids,
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("quarter" in error.lower() for error in errors)


@pytest.mark.parametrize("period", ["FY2026Q3", "Q3 2026", "Q3 FY2026", "FY2026第三财季"])
def test_correct_source_disclosed_fiscal_quarter_reaches_review(tools, period):
    ids = quarter_sources(tools, fiscal=True)
    reviewed = []

    def reviewer(question, claims):
        reviewed.extend(claims)
        return approve(question, claims)

    accepted, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", statement=f"{period}净利润同比改善。", evidence_ids=ids)]
        ),
        tools,
        "股票怎么样？",
        reviewer,
        "AAPL",
    )
    assert accepted and not errors
    assert reviewed[0]["source_quarter_labels"][0]["quarter"] == 3
    assert reviewed[0]["source_quarter_labels"][0]["end"] == "2026-06-27"


def test_bound_month_window_is_a_period_not_an_amount(tools):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="2026年4月至2026年6月单季度净利润同比改善。",
                    evidence_ids=ids,
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


@pytest.mark.parametrize("window", ["2026-04-01至06-30", "2026-04-01 至 2026-06-30"])
def test_bound_iso_range_can_abbreviate_end_year(tools, window):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis", statement=f"Q2（{window}）净利润同比改善。", evidence_ids=ids
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


def test_unresolved_date_digits_give_the_agent_a_source_bound_repair(tools):
    ids = quarter_sources(tools)
    claim = Claim(
        kind="analysis", statement="2026年二季度（4-6月）净利润同比改善。", evidence_ids=ids
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and "4-6" in errors[0]
    assert "2026-04-01 至 2026-06-30" in errors[0]
    claim.statement = "Q2（2026-04-01 至 2026-06-30）净利润同比改善。"
    accepted, errors = verify_submission(
        Submission(claims=[claim]),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


@pytest.mark.parametrize(
    "window",
    [
        "2026-05-01至06-30",
        "2026-04-01至06-29",
        "06-30",
        "2026-04-01至06-30USD",
        "2026-04-01至06-30倍",
    ],
)
def test_abbreviated_iso_range_requires_both_exact_endpoints_and_no_amount_suffix(tools, window):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=window, evidence_ids=ids)]),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize("text", ["Q2 2026美元", "Q2 2026倍", "Q2 2026%", "Q2 20260", "Q5 2026"])
def test_quarter_date_exception_keeps_amount_suffixes_and_invalid_labels(tools, text):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=text, evidence_ids=ids)]),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize(
    "text",
    [
        "2026年5月至2026年6月单季度净利润同比改善。",
        "2026年4月至2026年6月末单季度净利润同比改善。",
        "2026年4月至2026年6月净利润为2026元。",
        "2026年4月至2026年6月USD。",
    ],
)
def test_month_window_exception_needs_both_bound_months_and_keeps_amounts(tools, text):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=text, evidence_ids=ids)]),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize(
    "text",
    [
        "Q2 2026净利润同比改善。",
        "Q2净利润同比改善。",
        "FY2026第二财季净利润同比改善。",
        "2026年第2季度净利润同比改善。",
        "需核验FY2026Q2净利润已同比改善的持续性。",
    ],
)
def test_wrong_fiscal_quarter_does_not_reach_even_permissive_reviewer(tools, text):
    ids = quarter_sources(tools, fiscal=True)

    def unexpected(question, claims):
        raise AssertionError("Known quarter contradiction must be caught before review")

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=text, evidence_ids=ids)]),
        tools,
        "股票怎么样？",
        unexpected,
        "AAPL",
    )
    assert not accepted and any("quarter label" in error.lower() for error in errors)


def test_future_unobserved_quarter_is_a_follow_up_question(tools):
    ids = quarter_sources(tools, fiscal=True)
    accepted, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", statement="FY2026Q4尚需季度证据。", evidence_ids=ids)]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


@pytest.mark.parametrize(
    "source_text",
    [
        "Net income increased in the third quarter of fiscal 2026.",
        "Net income increased in the third quarter of fiscal 2026 ended May 27, 2026.",
        "Net income is forecast for the third quarter of fiscal 2026 ending September 27, 2026.",
    ],
)
def test_unbound_or_future_source_labels_are_not_local_proof(tools, source_text):
    ids = quarter_sources(tools, fiscal=True)
    tools.ledger[ids[1]]["text"] = source_text
    reached = []

    def reviewer(question, claims):
        reached.extend(claims)
        return approve(question, claims)

    verify_submission(
        Submission(
            claims=[Claim(kind="analysis", statement="FY2026Q2净利润同比改善。", evidence_ids=ids)]
        ),
        tools,
        "股票怎么样？",
        reviewer,
        "AAPL",
    )
    assert reached and reached[0]["source_quarter_labels"] == []


def test_narrative_quarter_conflict_uses_the_same_source_gate(tools):
    ids = quarter_sources(tools, fiscal=True)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(kind="narrative", statement="FY2026Q2净利润同比改善。", evidence_ids=[ids[1]])
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("quarter label" in error.lower() for error in errors)


@pytest.mark.parametrize(
    "suffix,expected",
    [
        ("尚不足以证成盈利加速。", True),
        ("尚不足以证成盈利加速，但净利润同比已加快。", False),
    ],
)
def test_captured_acceleration_limit_does_not_become_a_positive_assertion(tools, suffix, expected):
    ids = quarter_sources(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis", statement="Q2 2026净利润同比改善；" + suffix, evidence_ids=ids
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected
    assert bool(errors) is not expected


def test_chinese_fiscal_quarter_name_does_not_use_annual_growth(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    growth = tools.calculate("growth", "rev25", "rev24")["evidence"]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="FY2026第三财季营收同比改善。",
                    evidence_ids=[growth["id"]],
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("quarter period" in error for error in errors)


@pytest.mark.parametrize("quarter,expected", [(1, True), (3, False)])
def test_source_fiscal_year_is_not_calendar_year_of_quarter_end(tools, quarter, expected):
    ids = quarter_sources(tools, fiscal=True)
    filing = Filing.model_validate(tools.ledger[ids[1]]["filing"]).model_copy(
        update={
            "filed": date(2025, 10, 30),
            "period_end": date(2025, 9, 27),
        }
    )
    facts = [
        Fact.model_validate(
            {
                k: v
                for k, v in tools.ledger[f"label-net-{year}"].items()
                if k not in ("kind", "filing")
            }
        ).model_copy(
            update={
                "fiscal_year": year - 1,
                "start": date(year - 1, 6, 29),
                "end": date(year - 1, 9, 27),
            }
        )
        for year in (2026, 2025)
    ]
    passage = Passage.model_validate(
        {k: v for k, v in tools.ledger[ids[1]].items() if k not in ("kind", "filing")}
    ).model_copy(
        update={
            "text": "Net income increased in the first quarter of fiscal 2026 ended September 27, 2025.",
        }
    )
    tools.corpus.ingest(filing, facts, [passage])
    for item in [*facts, passage]:
        tools.ledger[item.id] = tools.corpus.evidence(item.id, tools.as_of)
    growth = tools.calculate("growth", facts[0].id, facts[1].id)["evidence"]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement=f"FY2026Q{quarter}净利润同比改善。",
                    evidence_ids=[growth["id"], passage.id],
                )
            ]
        ),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected
    assert bool(errors) is not expected
