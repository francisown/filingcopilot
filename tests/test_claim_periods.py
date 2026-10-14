"""Period-specific qualitative claims must bind their own observed comparisons."""

import pytest

from filingcopilot.models import Claim, Fact, Filing, Submission
from filingcopilot.verification import verify_submission


def approve(question, claims):
    return {
        "verdicts": [
            {"index": c["index"], "supported": True, "reason": "Controlled reviewer"}
            for c in claims
        ]
    }


@pytest.mark.parametrize(
    "statement,ids,expected",
    [
        ("季度净利润增速高于营业收入增速。", "both", True),
        ("季度净利润增速低于营业收入增速。", "both", False),
        ("季度净利润增速高于营业收入增速。", "profit", False),
        ("净利润增速高于营业收入增速。", "mixed", False),
        ("季度毛利润与净利润增速远超营业收入增速。", "coordinated", True),
        ("季度毛利润与净利润增速远超营业收入增速。", "both", False),
        ("无法判断季度净利润增速高于营业收入增速。", "profit", True),
    ],
)
def test_growth_ranking_binds_each_metric_and_same_source_windows(tools, statement, ids, expected):
    _, _, net, annual_revenue = observed(tools)
    revenue = tools.calculate("growth", "claim-revenue-2026", "claim-revenue-2025")["evidence"]
    gross = tools.calculate("growth", "claim-gross_profit-2026", "claim-gross_profit-2025")[
        "evidence"
    ]
    evidence_ids = {
        "both": [net["id"], revenue["id"]],
        "profit": [net["id"]],
        "mixed": [net["id"], annual_revenue["id"]],
        "coordinated": [gross["id"], net["id"], revenue["id"]],
    }[ids]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=statement, evidence_ids=evidence_ids)]),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected, errors
    assert bool(errors) is not expected


def observed(tools):
    current = tools.get_financial_fact("AAPL", "gross_profit", 2025)["evidence"]
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    previous = Fact.model_validate(
        {
            **{k: v for k, v in current.items() if k not in ("kind", "filing")},
            "id": "gross24",
            "value": 20,
            "fiscal_year": 2024,
            "start": "2023-10-01",
            "end": "2024-09-27",
        }
    )
    tools.corpus.add_facts(Filing.model_validate(current["filing"]), [previous])
    tools.ledger[previous.id] = tools.corpus.evidence(previous.id, tools.as_of)
    filing = Filing(
        id="AAPL:claim-quarter",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="claim-quarter",
        form="10-Q",
        filed="2026-07-31",
        period_end="2026-06-30",
        source_url="https://www.sec.gov/Archives/claim-quarter.htm",
        content_sha256="d" * 64,
    )
    facts = [
        Fact(
            id=f"claim-{key}-{year}",
            filing_id=filing.id,
            ticker="AAPL",
            metric=key,
            label=tools.domain.metrics[key].label,
            concept="us-gaap:Fixture",
            value=value,
            unit="USD",
            fiscal_year=year,
            start=f"{year}-04-01",
            end=f"{year}-06-30",
            source_url=filing.source_url,
            locator="synthetic",
        )
        for year in (2026, 2025)
        for key, value in [
            ("revenue", 200 if year == 2026 else 100),
            ("gross_profit", 100 if year == 2026 else 30),
            ("net_income", 60 if year == 2026 else 20),
        ]
    ]
    tools.corpus.ingest(filing, facts, [])
    for fact in facts:
        tools.ledger[fact.id] = tools.corpus.evidence(fact.id, tools.as_of)
    annual = tools.calculate("ratio", "gross25", "rev25")["evidence"]
    quarter = tools.calculate("growth", "claim-net_income-2026", "claim-net_income-2025")[
        "evidence"
    ]
    annual_growth = tools.calculate("growth", "rev25", "rev24")["evidence"]
    annual_prior = tools.calculate("ratio", "gross24", "rev24")["evidence"]
    return annual, annual_prior, quarter, annual_growth


@pytest.mark.parametrize(
    "statement,source",
    [
        ("最新季度毛利率改善。", "annual"),
        ("年度与最新季度均显示盈利与利润率同步改善。", "mixed"),
        ("需观察到与最新季度盈利跳升方向一致的估值支持。", "annual_growth"),
        ("季度利润增长说明TTM盈利持续增长。", "quarter"),
    ],
)
def test_other_period_comparison_cannot_support_explicit_period_claim(tools, statement, source):
    annual, annual_prior, quarter, annual_growth = observed(tools)
    ids = {
        "annual": [annual["id"], annual_prior["id"]],
        "mixed": [annual["id"], annual_prior["id"], quarter["id"]],
        "annual_growth": [annual_growth["id"]],
        "quarter": [quarter["id"]],
    }[source]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", statement=statement, evidence_ids=ids)]),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("period" in e.lower() for e in errors)


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("最新季度毛利率改善。", True),
        ("最新季度毛利率下降。", False),
        ("年度毛利率与最新季度毛利率均改善。", True),
        ("季度利润增长，尚不足以证明TTM盈利持续增长。", True),
        ("需观察TTM盈利是否持续增长。", True),
    ],
)
def test_period_specific_compares_and_scoped_limits(tools, statement, expected):
    annual, prior, quarter, _ = observed(tools)
    margins = [
        tools.calculate("ratio", f"claim-gross_profit-{y}", f"claim-revenue-{y}")["evidence"]
        for y in (2026, 2025)
    ]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement=statement,
                    evidence_ids=[e["id"] for e in (annual, prior, quarter, *margins)],
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert bool(accepted) is expected
    assert bool(errors) is not expected


def test_overview_exposes_both_source_disclosed_quarter_margins(tools):
    observed(tools)
    overview = tools.get_quarterly_overview("AAPL")
    margins = [
        tools.ledger[e["id"]]
        for e in overview["evidence"]
        if e.get("operation") == "ratio" and e["label"] == "毛利率"
    ]
    assert len(margins) == 2
    assert [e["inputs"][0]["start"] for e in margins] == ["2026-04-01", "2025-04-01"]
    assert [e["value"] for e in margins] == ["50.0", "30.0"]


def test_prior_loss_still_allows_margin_comparison_without_growth_percentage(tools):
    observed(tools)
    source = tools.ledger["claim-net_income-2025"]
    previous = Fact.model_validate(
        {**{k: v for k, v in source.items() if k not in ("kind", "filing")}, "value": "-20"}
    )
    facts = [
        Fact.model_validate({k: v for k, v in item.items() if k not in ("kind", "filing")})
        for item in tools.ledger.values()
        if item["kind"] == "fact"
        and item["filing_id"] == previous.filing_id
        and item["id"] != previous.id
    ]
    tools.corpus.ingest(Filing.model_validate(source["filing"]), [*facts, previous], [])
    overview = tools.get_quarterly_overview("AAPL")
    margins = [
        tools.ledger[e["id"]]
        for e in overview["evidence"]
        if e.get("operation") == "ratio" and e["label"] == "净利率"
    ]
    assert [e["value"] for e in margins] == ["30.0", "-20.0"]
    assert not any(
        e.get("operation") == "growth" and e["label"] == "净利润同比增长率"
        for e in overview["evidence"]
    )
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement="最新季度净利率改善。",
                    evidence_ids=[e["id"] for e in margins],
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


def test_current_ttm_amount_alone_cannot_establish_ttm_growth(tools):
    observed(tools)
    ttm = tools.calculate("trailing_sum", "rev25")["evidence"]
    accepted, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", statement="TTM营收增长。", evidence_ids=[ttm["id"]])]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("ttm period" in e for e in errors)


def test_ytd_income_does_not_support_quarter_claim(tools):
    _, _, growth, _ = observed(tools)
    # A same-year cumulative window is a different period, even with the same end.
    for item in tools.ledger[growth["id"]]["inputs"]:
        item["start"] = item["start"][:4] + "-01-01"
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis", statement="最新季度净利润增长。", evidence_ids=[growth["id"]]
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("period" in e for e in errors)
