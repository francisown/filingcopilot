"""ADS earnings must come from the reported share-class context, not ordinary EPS."""

import copy
import json
from datetime import date
from decimal import Decimal

import pytest
from test_foreign_financials import (
    CIK,
    NativeFetcher,
    annual_html,
    context,
    fraction,
    native_filing,
)
from test_market import NOW, URL, provider

from filingcopilot.audit import audit_package, digest
from filingcopilot.ifrs_sources import extract_ifrs_facts
from filingcopilot.market import parse_chart
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

AXIS = "ifrs-full:ClassesOfShareCapitalAxis"
MEMBER = "tsm:AmericanDepositarySharesMember"


@pytest.fixture
def native_service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.register_company("TSM", {"cik": CIK, "name": "TSMC"})
    service.refresh_domain()
    service.discovery.fetcher = NativeFetcher(tmp_path / "sec-cache")
    return service


def ads_html(*, member=MEMBER, extra_dimension=False, value="10.43", usd=True):
    segment = (
        f'<xbrli:segment><xbrldi:explicitMember dimension="{AXIS}">{member}</xbrldi:explicitMember>'
    )
    if extra_dimension:
        segment += '<xbrldi:explicitMember dimension="tsm:RegionAxis">tsm:OtherMember</xbrldi:explicitMember>'
    segment += "</xbrli:segment>"
    extra = context("ads25", segment=segment)
    extra += (
        '<xbrli:unit id="usd-eps"><xbrli:divide><xbrli:unitNumerator>'
        "<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>"
        "<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>"
        "</xbrli:unitDenominator></xbrli:divide></xbrli:unit>"
    )
    extra += fraction(
        "ordinary-usd",
        "ifrs-full:DilutedEarningsLossPerShare",
        "2.09",
        unit="usd-eps",
        scale=0,
        decimals="2",
    )
    extra += fraction(
        "ads-usd",
        "ifrs-full:DilutedEarningsLossPerShare",
        value,
        ctx="ads25",
        unit="usd-eps" if usd else "eps",
        scale=0,
        decimals="2",
    )
    return annual_html(extra)


def prepare_ads(service, **kwargs):
    service.discovery.fetcher = NativeFetcher(
        service.settings.home / "sec-cache", ads_html(**kwargs)
    )
    service.discovery.prepare("TSM", date(2026, 10, 9), years=1)
    data = provider()
    data["chart"]["result"][0]["meta"].update(symbol="TSM", regularMarketPrice=457.99)
    snapshot = parse_chart(json.dumps(data).encode(), "TSM", "5Y", date(2026, 10, 8), NOW, URL)
    service.market.history = lambda *args: snapshot
    return service.tools(date(2026, 10, 8))


def test_reported_usd_ads_eps_is_distinct_from_native_and_translated_ordinary_eps():
    html = ads_html()
    facts = extract_ifrs_facts(html, native_filing(html))
    ads = [f for f in facts if f.metric == "ads_diluted_eps"]
    assert len(ads) == 1 and ads[0].value == Decimal("10.43")
    assert ads[0].unit == "USD/ADS" and "便利换算" in ads[0].label
    ordinary = next(f for f in facts if f.metric == "diluted_eps")
    assert ordinary.value == Decimal("2.40") and ordinary.unit == "TWD/shares"


@pytest.mark.parametrize(
    "kwargs", [dict(member="tsm:OtherMember"), dict(extra_dimension=True), dict(usd=False)]
)
def test_unmatched_ads_context_or_currency_is_withheld(kwargs):
    html = ads_html(**kwargs)
    assert not any(
        f.metric == "ads_diluted_eps" for f in extract_ifrs_facts(html, native_filing(html))
    )


def test_annual_ads_pe_runs_without_model_and_replays_original(native_service):
    prepare_ads(native_service)
    request = ResearchRequest(
        language="zh",
        question="年度市盈率是多少？", ticker="TSM", as_of=date(2026, 10, 8), mode="agent"
    )
    plan = native_service.research_plan(request)
    assert plan["ready"] and not plan["uses_model"]
    assert "便利换算" in plan["message"]
    answer = native_service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert "43.91 倍" in answer.answer[0]["text"]
    assert "便利换算" in answer.answer[0]["text"] and "非TTM" in answer.answer[0]["text"]
    package = native_service.export_package(answer.id)
    audit = audit_package(package, source_cache=native_service.settings.home / "sec-cache")
    assert audit["status"] == "consistent"
    assert audit["raw_sources"]["facts_checked"] == 1
    assert native_service.capabilities("TSM")["annual_ads_valuation"]


@pytest.mark.parametrize("case", ["negative", "split", "historical", "currency", "identity"])
def test_ads_pe_rejects_unmatched_price_or_earnings_basis(native_service, case):
    tools = prepare_ads(native_service, value="-10.43" if case == "negative" else "10.43")
    snapshot = tools.market.history()
    changes = (
        {"split_dates": [date(2026, 5, 1)]}
        if case == "split"
        else {"quote_type": "historical_close"}
        if case == "historical"
        else {"currency": "TWD"}
        if case == "currency"
        else {}
    )
    tools.market.history = lambda *args: snapshot.model_copy(update=changes)
    if case == "identity":
        eps = tools.get_financial_fact("TSM", "ads_diluted_eps", 2025)["evidence"]
        eps["filing"]["cik"] = "0000000001"
        market = tools.get_stock_market("TSM")
        with pytest.raises(ValueError):
            tools.calculate("annual_pe", market["evidence_id"], eps["id"])
    else:
        assert not tools.get_stock_valuation("TSM")["found"]


def test_native_supplier_ttm_is_not_relabelled_annual_pe(native_service):
    tools = prepare_ads(native_service)
    assert tools.get_stock_valuation("TSM")["found"]
    result = tools.get_provider_ttm_valuation("TSM")
    assert not result["found"] and "TTM" in result["reason"]


def test_resealed_ads_eps_tampering_fails_original_replay(native_service):
    tools = prepare_ads(native_service)
    answer = native_service.research(
        ResearchRequest(
            language="zh",
            question="年报美元 ADS 稀释每股收益是多少？",
            ticker="TSM",
            as_of=tools.as_of,
            mode="reference",
        )
    )
    assert answer.status == "answered"
    package = copy.deepcopy(native_service.export_package(answer.id))
    fact = next(e for e in package["research"]["evidence"] if e.get("metric") == "ads_diluted_eps")
    fact["value"] = "10.45"
    package["research"]["answer"][0]["text"] = package["research"]["answer"][0]["text"].replace(
        "10.43", "10.45"
    )
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    audit = audit_package(package, source_cache=native_service.settings.home / "sec-cache")
    assert audit["status"] == "invalid"
    assert any(
        c["code"] == "raw_inline_ifrs_fact" and c["status"] == "fail" for c in audit["checks"]
    )


def test_reported_ads_eps_does_not_create_growth_across_convenience_exchange_rates(native_service):
    tools = prepare_ads(native_service)
    current = tools.get_financial_fact("TSM", "ads_diluted_eps", 2025)["evidence"]
    prior = copy.deepcopy(current)
    prior.update(
        id="prior-ads", value="6.81", fiscal_year=2024, start="2024-01-01", end="2024-12-31"
    )
    tools.ledger[prior["id"]] = prior
    for operation in ("growth", "difference", "ratio"):
        with pytest.raises(ValueError, match="便利换算"):
            tools.calculate(operation, current["id"], prior["id"])


@pytest.mark.parametrize(
    "question",
    ["年报 ADS EPS 同比增长多少？", "ADS EPS growth?", "FY2024 和 FY2025 年度市盈率是多少？"],
)
def test_unimplemented_ads_comparisons_are_blocked_before_submission(native_service, question):
    plan = native_service.research_plan(ResearchRequest(language="zh", question=question, ticker="TSM"))
    assert not plan["ready"]


def test_missing_reported_ads_eps_does_not_synthesize_from_ordinary_eps(native_service):
    native_service.discovery.prepare("TSM", date(2026, 10, 9), years=1)
    tools = native_service.tools(date(2026, 10, 9))
    tools.market.history = lambda *args: pytest.fail(
        "Do not request price without matching ADS EPS"
    )
    result = tools.get_stock_valuation("TSM")
    assert not result["found"] and "单独披露" in result["reason"]


def test_conflicting_reported_ads_eps_is_withheld():
    html = ads_html().replace(
        b"</html>",
        fraction(
            "ads-conflict",
            "ifrs-full:DilutedEarningsLossPerShare",
            "10.45",
            ctx="ads25",
            unit="usd-eps",
            scale=0,
            decimals="2",
        ).encode()
        + b"</html>",
    )
    assert not any(
        f.metric == "ads_diluted_eps" for f in extract_ifrs_facts(html, native_filing(html))
    )
