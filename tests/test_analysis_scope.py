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


def cash_evidence(tools, current=-50, previous=-60):
    filing = Filing(
        id="AAPL:scope",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="scope",
        form="10-Q",
        filed="2026-07-31",
        period_end="2026-06-30",
        source_url="https://www.sec.gov/Archives/scope.htm",
        content_sha256="b" * 64,
    )
    facts = [
        Fact(
            id=f"scope-{metric}-{year}",
            filing_id=filing.id,
            ticker="AAPL",
            metric=metric,
            label=tools.domain.metrics[metric].label,
            concept="us-gaap:Fixture",
            value=value,
            unit="USD",
            fiscal_year=year,
            start=f"{year}-01-01",
            end=f"{year}-06-30",
            source_url=filing.source_url,
            locator="synthetic",
        )
        for year, net in [(2026, current), (2025, previous)]
        for metric, value in [("operating_cash_flow", 100), ("cash_capex", 100 - net)]
    ]
    tools.corpus.ingest(filing, facts, [])
    for fact in facts:
        tools.ledger[fact.id] = tools.corpus.evidence(fact.id, tools.as_of)
    values = [
        tools.calculate(
            "free_cash_flow", f"scope-operating_cash_flow-{y}", f"scope-cash_capex-{y}"
        )["evidence"]
        for y in (2026, 2025)
    ]
    change = tools.calculate("difference", values[0]["id"], values[1]["id"])["evidence"]
    return values[0], change


@pytest.mark.parametrize("period", ["2026上半年", "2026H1", "2026年H1", "2026年上半年"])
def test_bound_calendar_half_year_label_is_not_a_financial_amount(tools, period):
    value, change = cash_evidence(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement=f"{period}项目口径自由现金流仍为负。",
                    evidence_ids=[value["id"], change["id"]],
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


@pytest.mark.parametrize("period", ["2027H1", "2026H1倍", "2026上半年美元", "2026H1.5"])
def test_half_year_date_exemption_requires_bound_window_and_no_amount_suffix(tools, period):
    value, change = cash_evidence(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement=f"{period}项目口径自由现金流仍为负。",
                    evidence_ids=[value["id"], change["id"]],
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("qualitative" in error for error in errors)


def test_annual_fact_does_not_support_calendar_half_year_exemption(tools):
    annual = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis", statement="2025H1营业收入增长。", evidence_ids=[annual["id"]]
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize(
    "current,previous,statement,expected",
    [
        (-50, -60, "本项目口径自由现金流转负。", False),
        (-50, 40, "本项目口径自由现金流转负。", True),
        (50, -60, "本项目口径自由现金流转正。", True),
        (50, 60, "本项目口径自由现金流由负转正。", False),
        (-50, -60, "本项目口径自由现金流仍为负值。", True),
        (-50, 0, "本项目口径自由现金流由正转负。", False),
        (50, 0, "本项目口径自由现金流由负转正。", False),
    ],
)
def test_cashflow_sign_transition_needs_both_observed_periods(
    tools, current, previous, statement, expected
):
    value, change = cash_evidence(tools, current, previous)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis", statement=statement, evidence_ids=[value["id"], change["id"]]
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected
    assert bool(errors) is (not expected)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2026年9月", True),
        ("2026年09月", True),
        ("2026年8月", False),
        ("2026年9月5倍", False),
        ("2026年9月美元", False),
    ],
)
def test_month_label_requires_bound_update_date_and_no_amount_suffix(tools, label, expected):
    filing = Filing(
        id="AAPL:month-event",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="month-event",
        form="8-K",
        filed="2026-09-15",
        period_end="2026-09-14",
        source_url="https://www.sec.gov/Archives/month-event.htm",
        content_sha256="e" * 64,
    )
    passage = Passage(
        id="month-event",
        ticker="AAPL",
        filing_id=filing.id,
        section="Current report",
        text="The company issued notes.",
        source_url=filing.source_url,
        locator="synthetic",
    )
    tools.corpus.ingest(filing, [], [passage])
    tools.ledger[passage.id] = tools.corpus.evidence(passage.id, tools.as_of)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis", statement=f"{label}公司发行了债券。", evidence_ids=[passage.id]
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected
    assert bool(errors) is (not expected)


def test_sign_transition_rule_applies_to_registry_metric_and_requires_comparison(tools):
    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    previous = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    growth = tools.calculate("growth", current["id"], previous["id"])["evidence"]
    accepted, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", statement="营业收入转负。", evidence_ids=[growth["id"]])]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("sign transition" in error for error in errors)
    value, _ = cash_evidence(tools, -50, 40)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(kind="analysis", statement="自由现金流转负。", evidence_ids=[value["id"]])
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("sign transition" in error for error in errors)


@pytest.mark.parametrize(
    "event_day,wording,expected",
    [
        ("2026-09-14", "同期公司发行了债券。", False),
        ("2026-05-14", "同期公司发行了债券。", True),
        ("2026-09-14", "此后公司发行了债券。", True),
        ("2026-09-14", "同期仍发债支撑扩张性支出。", False),
        ("2026-05-14", "同期公司发债。", True),
    ],
)
def test_update_event_cannot_be_called_same_period_outside_cash_window(
    tools, event_day, wording, expected
):
    value, change = cash_evidence(tools)
    filing = Filing(
        id="AAPL:event",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="event",
        form="8-K",
        filed=event_day,
        period_end=event_day,
        source_url="https://www.sec.gov/Archives/event.htm",
        content_sha256="c" * 64,
    )
    passage = Passage(
        id="scope-event",
        ticker="AAPL",
        filing_id=filing.id,
        section="Current report",
        text="The company issued notes.",
        source_url=filing.source_url,
        locator="synthetic",
    )
    tools.corpus.ingest(filing, [], [passage])
    tools.ledger[passage.id] = tools.corpus.evidence(passage.id, tools.as_of)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="年内累计自由现金流仍为负；" + wording,
                    evidence_ids=[value["id"], change["id"], passage.id],
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected
    assert bool(errors) is (not expected)


def test_period_labels_never_call_non_ascii_strftime(tools, monkeypatch):
    """Windows strftime encodes its format string with the ANSI codepage, so a
    non-ASCII format such as "%Y年%m月%d日" raises UnicodeEncodeError under
    non-CJK locales (the en-US CI runner). Period labels must be formatted
    manually, and this guard fails if a non-ASCII strftime format creeps back
    into the verification path."""
    from datetime import date as real_date

    from filingcopilot import verification

    class GuardedDate(real_date):
        def strftime(self, format, *args):
            assert format.isascii(), f"strftime format must stay ASCII: {format!r}"
            return super().strftime(format, *args)

    monkeypatch.setattr(verification, "date", GuardedDate)
    filing = Filing(
        id="AAPL:locale-guard",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="locale-guard",
        form="8-K",
        filed="2026-09-15",
        period_end="2026-09-14",
        source_url="https://www.sec.gov/Archives/locale-guard.htm",
        content_sha256="f" * 64,
    )
    passage = Passage(
        id="locale-guard",
        ticker="AAPL",
        filing_id=filing.id,
        section="Current report",
        text="The company issued notes.",
        source_url=filing.source_url,
        locator="synthetic",
    )
    tools.corpus.ingest(filing, [], [passage])
    tools.ledger[passage.id] = tools.corpus.evidence(passage.id, tools.as_of)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="2026年09月公司发行了债券。",
                    evidence_ids=[passage.id],
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is True
    assert bool(errors) is False
