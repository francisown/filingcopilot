import hashlib
import json
from datetime import date
from decimal import Decimal

import pytest

from filingcopilot.audit import audit_package, digest
from filingcopilot.domain import load_domain
from filingcopilot.ifrs_sources import extract_ifrs_facts
from filingcopilot.ingestion import acquire_company
from filingcopilot.models import Fact, Filing, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

CIK = "0001046179"
URL = "https://www.sec.gov/Archives/edgar/data/1046179/000000000026000001/annual.htm"


def context(key, year=2025, *, segment="", cik=CIK, instant=False):
    period = (
        f"<xbrli:instant>{year}-12-31</xbrli:instant>"
        if instant
        else (
            f"<xbrli:startdate>{year}-01-01</xbrli:startdate><xbrli:enddate>{year}-12-31</xbrli:enddate>"
        )
    )
    return (
        f'<xbrli:context id="{key}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
        f"{cik}</xbrli:identifier>{segment}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>"
    )


def fraction(key, concept, value, *, ctx="c25", unit="twd", scale=6, decimals="-5", extra=""):
    return (
        f'<ix:nonfraction id="{key}" name="{concept}" contextref="{ctx}" unitref="{unit}" '
        f'format="ixt:num-dot-decimal" scale="{scale}" decimals="{decimals}" {extra}>{value}</ix:nonfraction>'
    )


def annual_html(extra=""):
    resources = context("c25") + context("c24", 2024) + context("balance", instant=True)
    resources += context("segment", segment="<xbrli:segment>Customer</xbrli:segment>")
    resources += context("wrong", cik="0000000001") + context("future", 2026)
    units = '<xbrli:unit id="twd"><xbrli:measure>iso4217:TWD</xbrli:measure></xbrli:unit>'
    units += '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    units += (
        '<xbrli:unit id="eps"><xbrli:divide><xbrli:unitnumerator><xbrli:measure>iso4217:TWD</xbrli:measure>'
        "</xbrli:unitnumerator><xbrli:unitdenominator><xbrli:measure>xbrli:shares</xbrli:measure>"
        "</xbrli:unitdenominator></xbrli:divide></xbrli:unit>"
    )
    values = fraction("revenue", "ifrs-full:RevenueFromContractsWithCustomers", "1,200.0")
    values += fraction(
        "rounded", "ifrs-full:RevenueFromContractsWithCustomers", "1,200", decimals="-6"
    )
    values += fraction("prior", "ifrs-full:RevenueFromContractsWithCustomers", "1,000.0", ctx="c24")
    values += fraction(
        "translation", "ifrs-full:RevenueFromContractsWithCustomers", "40.0", unit="usd"
    )
    for key in ("segment", "wrong", "future"):
        values += fraction(
            key + "-revenue", "ifrs-full:RevenueFromContractsWithCustomers", "9,999.0", ctx=key
        )
    values += fraction("gross", "ifrs-full:GrossProfit", "700.0")
    values += fraction("owners", "ifrs-full:ProfitLossAttributableToOwnersOfParent", "200.0")
    values += fraction("group", "ifrs-full:ProfitLoss", "230.0")
    values += fraction(
        "eps-value",
        "ifrs-full:DilutedEarningsLossPerShare",
        "2.40",
        unit="eps",
        scale=0,
        decimals="2",
    )
    values += fraction("assets", "ifrs-full:Assets", "2,300.0", ctx="balance")
    return (
        "<html><ix:header><ix:resources>"
        + resources
        + units
        + "</ix:resources></ix:header>"
        + "<p>Risk Factors</p><p>"
        + "Competition may affect demand. " * 10
        + "</p>"
        + values
        + extra
        + "</html>"
    ).encode()


class NativeFetcher:
    def __init__(self, cache, html=None):
        self.cache, self.html, self.calls = cache, html or annual_html(), []

    def fetch(self, url, offline=False, max_age=None):
        self.calls.append(url)
        if "/submissions/" in url:
            content = json.dumps(
                {
                    "cik": CIK,
                    "tickers": ["TSM"],
                    "filings": {
                        "files": [],
                        "recent": {
                            "form": ["20-F"],
                            "filingDate": ["2026-04-16"],
                            "reportDate": ["2025-12-31"],
                            "accessionNumber": ["0000000000-26-000001"],
                            "primaryDocument": ["annual.htm"],
                        },
                    },
                }
            ).encode()
        elif "company_tickers" in url:
            content = json.dumps(
                {"0": {"cik_str": 1046179, "ticker": "TSM", "title": "TSMC"}}
            ).encode()
        elif "/companyfacts/" in url:
            raise AssertionError(
                "Native IFRS extraction must use the bound original, not convenient USD rows"
            )
        else:
            content = self.html
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")).write_bytes(content)
        return content


@pytest.fixture
def native_service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.register_company("TSM", {"cik": CIK, "name": "TSMC"})
    service.refresh_domain()
    service.discovery.fetcher = NativeFetcher(tmp_path / "sec-cache")
    return service


def test_acquisition_reads_native_consolidated_ifrs_with_source_scale_and_share_basis(tmp_path):
    domain = load_domain(companies={"TSM": {"cik": CIK, "name": "TSMC"}})
    entries = acquire_company(
        NativeFetcher(tmp_path),
        "TSM",
        1,
        domain=domain,
        as_of=date(2026, 10, 9),
        forms=("20-F", "20-F/A"),
    )
    facts = [Fact.model_validate(f) for f in entries[0]["facts"]]
    revenue = [f for f in facts if f.metric == "revenue" and f.fiscal_year == 2025]
    assert len(revenue) == 1 and revenue[0].value == Decimal("1200000000")
    assert revenue[0].unit == "TWD" and revenue[0].source_url == URL
    eps = next(f for f in facts if f.metric == "diluted_eps")
    assert eps.unit == "TWD/shares" and "普通股" in eps.label
    net = next(f for f in facts if f.metric == "net_income")
    assert net.value == Decimal("200000000") and "母公司" in net.label


def test_native_annual_numbers_and_ratios_use_shared_reference_verifier_and_raw_audit(
    native_service,
):
    service = native_service
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="TSM FY2025 营收同比和毛利率",
            ticker="TSM",
            as_of=date(2026, 10, 9),
            mode="reference",
            auto_acquire=True,
        )
    )
    assert answer.status == "answered" and "新台币" in answer.answer[0]["text"]
    assert {
        Decimal(e["value"]).quantize(Decimal(".01"))
        for e in answer.evidence
        if e["kind"] == "calculation"
    } == {Decimal(20), Decimal("58.33")}
    package = service.export_package(answer.id)
    audit = audit_package(
        package,
        corpus=service.corpus,
        trusted_domain=service.domain,
        source_cache=service.settings.home / "sec-cache",
    )
    assert audit["status"] == "consistent" and audit["raw_sources"]["facts_checked"] >= 3
    package["research"]["evidence"][0]["unit"] = "USD"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert (
        audit_package(package, source_cache=service.settings.home / "sec-cache")["status"]
        != "consistent"
    )


def test_cached_annual_enrichment_preserves_original_passages(native_service):
    service = native_service
    html = annual_html()
    filing = Filing(
        id="TSM:old",
        ticker="TSM",
        company="TSMC",
        cik=CIK,
        accession="old",
        form="20-F",
        filed="2026-04-16",
        period_end="2025-12-31",
        source_url=URL,
        content_sha256=hashlib.sha256(html).hexdigest(),
    )
    from filingcopilot.ingestion import extract_passages

    service.corpus.ingest(filing, [], extract_passages(html, filing))
    service.discovery.fetcher.fetch(URL)
    before = [p.model_dump() for p in service.corpus.passages(date(2026, 10, 9), "TSM")]
    result = service.tools(date(2026, 10, 9)).get_financial_fact("TSM", "revenue", 2025)
    assert result["found"] and result["evidence"]["unit"] == "TWD"
    assert before == [p.model_dump() for p in service.corpus.passages(date(2026, 10, 9), "TSM")]


@pytest.mark.parametrize(
    "question",
    [
        "最新季度营收是多少？",
        "TTM 营业利润率是多少？",
        "当前 TTM EPS 市盈率是多少？",
        "季度自由现金流是多少？",
    ],
)
def test_native_scope_does_not_promise_quarters_ads_valuation_or_unmapped_cashflows(
    native_service, question
):
    plan = native_service.research_plan(
        ResearchRequest(language="zh", question=question, ticker="TSM", mode="reference")
    )
    assert not plan["ready"] and any("年报" in q for q in plan["suggestions"])


def cash_html(*, capex=True, ocf_sign="", capex_sign=""):
    extra = ""
    for ctx, cash, purchases in (("c25", "500.0", "300.0"), ("c24", "400.0", "250.0")):
        extra += fraction(
            ctx + "-ocf",
            "ifrs-full:CashFlowsFromUsedInOperatingActivities",
            cash,
            ctx=ctx,
            extra=ocf_sign,
        )
        if capex:
            # Parentheses in the statement are presentation, not an XBRL sign.
            extra += (
                "("
                + fraction(
                    ctx + "-capex",
                    "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                    purchases,
                    ctx=ctx,
                    extra=capex_sign,
                )
                + ")"
            )
    extra += fraction("noncash", "ifrs-full:AdditionsToPropertyPlantAndEquipment", "999.0")
    extra += fraction(
        "translated-ocf", "ifrs-full:CashFlowsFromUsedInOperatingActivities", "999.0", unit="usd"
    )
    return annual_html(extra)


def prepare_cash(service, **kwargs):
    service.discovery.fetcher.html = cash_html(**kwargs)
    service.discovery.prepare("TSM", date(2026, 10, 9))
    return service.tools(date(2026, 10, 9))


@pytest.mark.parametrize(
    "question", ["年报自由现金流、现金资本开支和同比是多少？", "自由现金流是多少？"]
)
def test_native_cashflow_numbers_use_annual_original_without_model_or_quarter_refresh(
    native_service, question
):
    service = native_service
    prepare_cash(service)
    service.discovery.prepare_quarterly = lambda *a, **kw: pytest.fail(
        "Must not request 10-Q for native annual cash flow"
    )
    service.gateway = object()  # A selected Agent mode must still use the numeric path.
    request = ResearchRequest(language="zh", question=question, ticker="TSM", mode="agent")
    plan = service.research_plan(request)
    assert plan["ready"] and not plan["uses_model"] and "20-F" in plan["message"]
    answer = service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    result = next(t["result"] for t in answer.trace if t.get("tool") == "get_cashflow")
    assert result["period_scope"] == "annual" and result["end"] == "2025-12-31"
    fcf = next(e for e in answer.evidence if e["id"] == result["current_id"])
    assert Decimal(fcf["value"]) == Decimal("200000000") and fcf["unit"] == "TWD"
    assert [Decimal(e["value"]) for e in fcf["inputs"]] == [
        Decimal("500000000"),
        Decimal("300000000"),
    ]
    assert "政府补助" in result["definition_note"] and "IASB" in result["definition_note"]
    package = service.export_package(answer.id)
    audit = audit_package(
        package,
        corpus=service.corpus,
        trusted_domain=service.domain,
        source_cache=service.settings.home / "sec-cache",
    )
    assert audit["status"] == "consistent" and audit["raw_sources"]["facts_checked"] >= 4


@pytest.mark.parametrize("period", ["quarter", "ytd", "ttm"])
def test_native_cashflow_tool_does_not_replace_requested_period_with_annual(native_service, period):
    tools = prepare_cash(native_service)
    result = tools.get_cashflow("TSM", period)
    assert not result["found"] and "6-K" in result["reason"] and not tools.ledger


@pytest.mark.parametrize(
    "question",
    [
        "年报净资本开支和自由现金流是多少？",
        "年报现金资本开支扣除政府补助是多少？",
        "2025 半年自由现金流是多少？",
        "季度自由现金流是多少？",
        "TTM 自由现金流是多少？",
        "累计自由现金流是多少？",
        "FY2024 和 FY2025 自由现金流是多少？",
    ],
)
def test_native_unimplemented_cashflow_scope_is_blocked_before_submission(native_service, question):
    plan = native_service.research_plan(ResearchRequest(language="zh", question=question, ticker="TSM"))
    assert not plan["ready"]
    assert "自由现金流" in plan["suggestions"][0]


def test_native_missing_cash_purchases_are_not_replaced_with_noncash_additions_or_zero(
    native_service,
):
    tools = prepare_cash(native_service, capex=False)
    result = tools.get_cashflow("TSM", "annual")
    assert not result["found"] and "现金 PP&E" in result["reason"]


def test_native_signed_cashflow_keeps_negative_ocf_and_rejects_negative_payment(native_service):
    tools = prepare_cash(native_service, ocf_sign='sign="-"')
    result = tools.get_cashflow("TSM", "annual")
    assert result["found"] and Decimal(tools.ledger[result["current_id"]]["value"]) == Decimal(
        "-800000000"
    )
    # A source-signed negative purchase must not be silently made positive.
    from filingcopilot.models import Filing

    filing = Filing.model_validate(native_service.corpus.catalog(tools.as_of, "TSM")[0])
    facts = extract_ifrs_facts(cash_html(capex_sign='sign="-"'), filing, tools.domain)
    assert next(
        f for f in facts if f.metric == "cash_capex" and f.fiscal_year == 2025
    ).value == Decimal("-300000000")


def test_additive_native_profile_preserves_frozen_old_package_but_rejects_changed_metric(
    native_service,
):
    service = native_service
    answer = service.research(
        ResearchRequest(language="zh", question="FY2025 营收是多少？", ticker="TSM", auto_acquire=True)
    )
    package = service.export_package(answer.id)
    profile = package["domain"]["financial_profiles"]["TSM"]
    for key in ("operating_cash_flow", "cash_capex"):
        profile["metrics"].pop(key, None)
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package, trusted_domain=service.domain)["status"] == "consistent"
    profile["metrics"]["revenue"]["label"] = "Tampered label"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    audit = audit_package(package, trusted_domain=service.domain)
    assert any(c["code"] == "domain" and c["status"] == "fail" for c in audit["checks"])


def test_negative_cash_payment_returns_gap_without_silent_absolute_value(native_service):
    tools = prepare_cash(native_service, capex_sign='sign="-"')
    result = tools.get_cashflow("TSM", "annual")
    assert not result["found"] and any("非负" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "question", ["年报净经营现金流是多少？", "annual net operating cash flow 是多少？"]
)
def test_net_operating_cash_is_not_confused_with_unimplemented_net_capex(native_service, question):
    plan = native_service.research_plan(ResearchRequest(language="zh", question=question, ticker="TSM"))
    assert plan["ready"] and plan["annual_numeric_direct"] and not plan["uses_model"]


def test_native_cashflow_original_replay_rejects_resealed_changed_payment(native_service):
    prepare_cash(native_service)
    answer = native_service.research(
        ResearchRequest(language="zh", question="年报自由现金流是多少？", ticker="TSM")
    )
    package = native_service.export_package(answer.id)

    def change(obj):
        if isinstance(obj, dict):
            if obj.get("metric") == "cash_capex" and obj.get("fiscal_year") == 2025:
                obj["value"] = "310000000"
            for value in obj.values():
                change(value)
        elif isinstance(obj, list):
            for value in obj:
                change(value)

    change(package)
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    audit = audit_package(package, source_cache=native_service.settings.home / "sec-cache")
    assert any(
        c["code"] == "raw_inline_ifrs_fact" and c["status"] == "fail" for c in audit["checks"]
    )


def test_native_numeric_agent_request_uses_code_without_model(native_service):
    class NoModel:
        def chat(self, *args, **kwargs):
            raise AssertionError("Pure annual numeric queries should not wait on a model")

    service = native_service
    service.gateway = NoModel()
    request = ResearchRequest(
        language="zh",
        question="TSM FY2025 营收同比和毛利率",
        ticker="TSM",
        as_of=date(2026, 10, 9),
        mode="agent",
        auto_acquire=True,
    )
    assert service.research_plan(request)["uses_model"] is False
    answer = service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0


def native_filing(html):
    return Filing(
        id="TSM:parser",
        ticker="TSM",
        company="TSMC",
        cik=CIK,
        accession="parser",
        form="20-F",
        filed="2026-04-16",
        period_end="2025-12-31",
        source_url=URL,
        content_sha256=hashlib.sha256(html).hexdigest(),
    )


@pytest.mark.parametrize(
    "conflict",
    [
        fraction("conflict", "ifrs-full:RevenueFromContractsWithCustomers", "1,201.0"),
        fraction("conflict", "ifrs-full:RevenueFromContractsWithCustomers", "1,210", decimals="-6"),
        fraction("revenue", "ifrs-full:RevenueFromContractsWithCustomers", "1,200.0"),
    ],
)
def test_conflicting_statement_numbers_or_duplicate_source_ids_withhold_metric(conflict):
    html = annual_html(conflict)
    facts = extract_ifrs_facts(html, native_filing(html))
    # A duplicate exact node ID must not become a trusted locator. Its remaining
    # rounded disclosure can still be valid; a numeric conflict must be withheld.
    if 'id="revenue"' in conflict:
        assert all(f.locator != "inline-ifrs:id=revenue; decimals=-5" for f in facts)
    else:
        assert not any(f.metric == "revenue" and f.fiscal_year == 2025 for f in facts)


def test_native_loss_sign_is_preserved_without_using_group_profit_or_usd_eps():
    html = (
        annual_html()
        .replace(b'id="owners"', b'sign="-" id="owners"')
        .replace(b'id="eps-value"', b'sign="-" id="eps-value"')
    )
    facts = extract_ifrs_facts(html, native_filing(html))
    assert next(f for f in facts if f.metric == "net_income").value == Decimal("-200000000")
    assert next(f for f in facts if f.metric == "diluted_eps").value == Decimal("-2.40")


def test_native_annual_sources_respect_known_date_and_do_not_make_ads_valuation(native_service):
    native_service.discovery.prepare("TSM", date(2026, 10, 9), years=1)
    tools = native_service.tools(date(2026, 4, 15))
    assert not tools.get_financial_fact("TSM", "revenue", 2025)["found"]
    assert not native_service.tools(date(2026, 10, 9)).get_stock_valuation("TSM")["found"]


def test_native_provider_pe_requires_share_basis_bridge_before_supplier_request(native_service):
    class NoSupplier:
        def now(self):
            raise AssertionError("Do not read a supplier before ADS basis is supported")

    tools = native_service.tools(date(2026, 10, 9))
    tools.market = NoSupplier()
    assert not tools.get_provider_ttm_valuation("TSM")["found"]
    assert not tools.ledger
