import copy
import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.agent import ResearchAgent
from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, create_package, digest
from filingcopilot.market import MarketData, market_evidence, parse_chart
from filingcopilot.models import Claim, Fact, Filing, ResearchRequest, Submission
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.valuation import ValuationEvidence, parse_eps, valuation_evidence
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

NOW = datetime.now().astimezone()
DAY = NOW.date()
END = DAY - timedelta(days=60)
EPS_URL = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL?type=trailingDilutedEPS"
PRICE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?interval=1d"


@pytest.fixture(autouse=True)
def fixture_clock(fixed_fixture_date):
    fixed_fixture_date(DAY)


def eps_body(value=4, currency="USD"):
    return {
        "timeseries": {
            "result": [
                {
                    "meta": {"symbol": ["AAPL"], "type": ["trailingDilutedEPS"]},
                    "trailingDilutedEPS": [
                        {
                            "asOfDate": END.isoformat(),
                            "periodType": "TTM",
                            "currencyCode": currency,
                            "reportedValue": {"raw": value},
                        }
                    ],
                }
            ],
            "error": None,
        }
    }


def price_body():
    stamp = int((NOW - timedelta(days=1)).timestamp())
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "AAPL",
                        "currency": "USD",
                        "instrumentType": "EQUITY",
                        "longName": "Apple Inc.",
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": 100,
                        "regularMarketTime": stamp,
                    },
                    "timestamp": [stamp],
                    "indicators": {"quote": [{"close": [99]}], "adjclose": [{"adjclose": [99]}]},
                }
            ],
            "error": None,
        }
    }


@pytest.fixture
def service(tmp_path):
    result = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))

    def handler(request):
        return httpx.Response(
            200, json=eps_body() if "fundamentals-timeseries" in str(request.url) else price_body()
        )

    result.market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    return result


def parse(data=None):
    return parse_eps(json.dumps(data or eps_body()).encode(), "AAPL", NOW, EPS_URL)


def test_eps_is_supplier_labeled_not_fiscal_year_or_sec_fact(service):
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="供应商TTM每股收益和市盈率是多少？", ticker="AAPL", mode="reference"
        )
    )
    assert answer.status == "answered", answer.gaps
    assert [c["kind"] for c in answer.answer] == ["valuation", "calculation"]
    assert answer.usage["model_calls"] == 0
    assert "25.00 倍" in answer.answer[1]["text"]
    assert "供应商期末标签" in answer.answer[0]["text"] and "FY" not in answer.answer[0]["text"]
    package = service.export_package(answer.id)
    audit = service.audit_run(answer.id)
    assert audit["checks_failed"] == 0, audit
    assert audit["valuation_sources"]["checked"] == 1 and audit["market_sources"]["checked"] == 1
    assert audit_package(package)["checks_failed"] == 0


def test_eps_only_needs_no_price_request_and_preserves_loss(service):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=eps_body(-2))

    service.market.client = service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )
    answer = service.research(
        ResearchRequest(language="zh", question="TTM EPS是多少？", ticker="AAPL", mode="reference")
    )
    assert answer.status == "answered" and "-2.00" in answer.answer[0]["text"]
    assert len(calls) == 1 and "fundamentals-timeseries" in calls[0]


class AssessmentGateway:
    """Controlled tool selection and review, not a live model accuracy test."""

    def __init__(self, retry=False):
        self.context = None
        self.retry = retry
        self.calls = 0

    def complete(self, messages, schemas, review=False):
        if review:
            claims = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": c["index"],
                                    "supported": True,
                                    "reason": "Controlled review",
                                }
                                for c in claims
                            ]
                        }
                    )
                }
            }
        self.calls += 1
        self.context = json.loads(messages[1]["content"])
        if self.retry and self.calls == 1:
            name, arguments = "get_provider_ttm_valuation", {"ticker": "AAPL"}
        else:
            growth = next(
                e
                for e in self.context["annual_overview"]["evidence"]
                if e.get("operation") == "growth"
            )
            name, arguments = (
                "submit_answer",
                {
                    "claims": [
                        {
                            "kind": "analysis",
                            "evidence_ids": [growth["id"]],
                            "statement": "年度营收增长支持经营表现改善，后续需观察增长能否持续。",
                        }
                    ],
                    "gaps": [],
                },
            )
        return {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": str(self.calls),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        }


def add_annual_eps(service):
    filing = Filing(
        id="eps-annual",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="eps-annual",
        form="10-K",
        filed="2026-02-01",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/Archives/eps.htm",
        content_sha256="a" * 64,
    )
    fact = Fact(
        id="eps-annual-fact",
        filing_id=filing.id,
        ticker="AAPL",
        metric="diluted_eps",
        label="稀释每股收益",
        concept="us-gaap:EarningsPerShareDiluted",
        value="5",
        unit="USD/shares",
        fiscal_year=2025,
        start="2025-01-01",
        end="2025-12-31",
        source_url="https://data.sec.gov/eps.json",
        locator="controlled annual EPS",
    )
    service.corpus.ingest(filing, [fact], [])


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("供应商TTM市盈率与年度口径的差异主因投资收益。", False),
        (
            "供应商TTM市盈率与年度市盈率存在明显差距，因口径不同（供应商未重建SEC加权股数、未独立核对拆股调整），单一倍数均不能独立证明便宜；仍需验证增长可持续性、非经常性损益摊薄及未来现金回报假设。",
            False,
        ),
        ("供应商TTM与年度市盈率差距是由口径造成的。", False),
        ("两个市盈率差距源于投资收益。", False),
        ("两个市盈率差距来自股数变动。", False),
        ("市盈率差距因为年度与TTM使用不同口径。", False),
        ("两种市盈率口径不同，差距是否来自一次性损益仍需对账。", True),
        ("年度和供应商TTM市盈率使用不同期间的EPS，不能把差额直接视为盈利改善。", True),
        ("两种口径需独立对账，投资收益对正常化盈利的影响尚需核验。", True),
        ("经营改善支撑估值下限。", False),
        ("无法据这些倍数核验估值下限，需要明确假设与估值模型。", True),
        ("经营面支撑存在，但不等同于估值有安全边际。", True),
        ("经营改善不等同于估值有安全边际，但经营改善支撑估值下限。", False),
    ],
)
def test_two_multiples_do_not_prove_an_eps_bridge_or_valuation_floor(service, statement, expected):
    add_annual_eps(service)
    tools = service.tools(DAY)
    annual = tools.get_stock_valuation("AAPL")["evidence"]
    supplier = tools.get_provider_ttm_valuation("AAPL")["evidence"]

    reviewed = []

    def reviewer(question, claims):
        reviewed.extend(claims)
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "Controlled review"}
                for c in claims
            ]
        }

    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    evidence_ids=[annual["id"], supplier["id"]],
                    statement=statement,
                )
            ]
        ),
        tools,
        "苹果TTM市盈率贵吗？",
        reviewer,
        "AAPL",
    )
    assert bool(accepted) is expected and bool(errors) is (not expected)
    assert bool(reviewed) is expected


@pytest.mark.parametrize("question", ["苹果股票怎么样？", "苹果TTM市盈率贵吗？"])
def test_stock_assessment_gets_distinct_compact_valuations_and_auditable_details(service, question):
    add_annual_eps(service)
    gateway = AssessmentGateway()
    service.gateway = gateway
    plan = service.research_plan(ResearchRequest(language="zh", question=question, ticker="AAPL"))
    assert plan["ready"] and plan["kind"] == "stock" and plan["uses_model"]
    answer = service.research(ResearchRequest(language="zh", question=question, ticker="AAPL"))
    assert answer.mode == "agent" and answer.answer[0]["kind"] == "analysis"
    supplier = gateway.context["supplier_ttm_valuation"]
    assert supplier["found"] and supplier["evidence"]["value"] == "25"
    assert "inputs" not in supplier["evidence"] and "points" not in json.dumps(supplier)
    assert supplier["eps"]["snapshot"]["value"] == "4"
    assert gateway.context["annual_valuation"]["evidence"]["value"] == "20"
    operations = {e.get("operation") for e in answer.evidence}
    assert "provider_ttm_pe" in operations
    assert ("annual_pe" in operations) is ("TTM" not in question)
    assert any("已分别计算 SEC 年度及供应商 TTM" in gap for gap in answer.gaps)
    assert not any("尚未计算 TTM" in gap for gap in answer.gaps)
    report = service.audit_run(answer.id)
    assert report["checks_failed"] == 0, report
    assert report["valuation_sources"]["checked"] == 1
    assert report["market_sources"]["checked"] == 1


@pytest.mark.parametrize("case", ["negative", "eps_timeout", "price_timeout"])
def test_stock_assessment_retains_source_failure_and_other_evidence(service, case):
    calls = []

    def handler(request):
        eps = "fundamentals-timeseries" in str(request.url)
        calls.append(eps)
        if case == ("eps_timeout" if eps else "price_timeout"):
            raise httpx.ReadTimeout("controlled outage", request=request)
        return httpx.Response(
            200, json=eps_body(-2 if case == "negative" else 4) if eps else price_body()
        )

    service.market.client = service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )
    gateway = AssessmentGateway()
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL"), service.tools(DAY)
    )
    assert answer.answer[0]["kind"] == "analysis"
    assert any("供应商 TTM 市盈率本次未完成" in gap for gap in answer.gaps)
    assert not any(e.get("operation") == "provider_ttm_pe" for e in answer.evidence)
    assert any(e["kind"] == "valuation" for e in answer.evidence) is (case != "eps_timeout")
    assert any(e["kind"] == "market" for e in answer.evidence) is (case != "price_timeout")
    assert (
        audit_package(
            create_package(answer, service.domain, "AAPL"), market_cache=service.market.cache
        )["checks_failed"]
        == 0
    )
    assert True in calls


def test_stock_assessment_can_retry_failed_prefetch_and_updates_coverage_from_ledger(service):
    eps_calls = 0

    def handler(request):
        nonlocal eps_calls
        eps = "fundamentals-timeseries" in str(request.url)
        if eps:
            eps_calls += 1
            if eps_calls <= 2:
                return httpx.Response(502)
        return httpx.Response(200, json=eps_body() if eps else price_body())

    service.market.client = service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )
    gateway = AssessmentGateway(retry=True)
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL"), service.tools(DAY)
    )
    assert gateway.context["supplier_ttm_valuation"]["found"] is False
    assert eps_calls == 3 and gateway.calls == 2
    assert any(e.get("operation") == "provider_ttm_pe" for e in answer.evidence)
    assert any("已计算供应商 TTM" in gap for gap in answer.gaps)
    assert not any("TTM 市盈率本次未完成" in gap for gap in answer.gaps)


@pytest.mark.parametrize("scope", ["past_date", "explicit_fy"])
def test_historical_or_fiscal_stock_assessment_does_not_fetch_current_supplier_eps(service, scope):
    requests = []
    service.market.client = service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(str(r.url)) or httpx.Response(200, json=price_body())
        )
    )
    gateway = AssessmentGateway()
    day = DAY - timedelta(days=1) if scope == "past_date" else DAY
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(
            language="zh",
            question="苹果FY2025股票怎么样？" if scope == "explicit_fy" else "苹果股票怎么样？",
            ticker="AAPL",
            as_of=day,
        ),
        service.tools(day),
    )
    assert answer.answer
    assert "supplier_ttm_valuation" not in gateway.context
    assert not any("fundamentals-timeseries" in url for url in requests)


def test_large_market_history_stays_in_export_not_agent_context(service):
    data = price_body()
    result = data["chart"]["result"][0]
    # Fixed exchange-local closes avoid duplicate dates across DST and exclude
    # a future NY close when Asia has already moved to the next calendar day.
    exchange_time = NOW.astimezone(ZoneInfo("America/New_York"))
    last_close = exchange_time.date() - timedelta(days=exchange_time.hour < 16)
    stamps = [
        int(
            datetime.combine(
                last_close - timedelta(days=d), time(16), ZoneInfo("America/New_York")
            ).timestamp()
        )
        for d in range(1499, -1, -1)
    ]
    assert all(stamp <= NOW.timestamp() for stamp in stamps)
    result["timestamp"] = stamps
    result["indicators"] = {
        "quote": [{"close": [99] * len(stamps)}],
        "adjclose": [{"adjclose": [99] * len(stamps)}],
    }
    service.market.client = service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=eps_body() if "fundamentals-timeseries" in str(r.url) else data
            )
        )
    )
    gateway = AssessmentGateway()
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL"), service.tools(DAY)
    )
    assert len(json.dumps(gateway.context["supplier_ttm_valuation"])) < 3000
    pe = next(e for e in answer.evidence if e.get("operation") == "provider_ttm_pe")
    assert len(pe["inputs"][0]["snapshot"]["points"]) == 1500
    assert (
        audit_package(
            create_package(answer, service.domain, "AAPL"), market_cache=service.market.cache
        )["checks_failed"]
        == 0
    )


def test_extra_supplier_tool_call_does_not_pollute_explicit_fiscal_assessment(service):
    gateway = AssessmentGateway(retry=True)
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(language="zh", question="苹果FY2025股票怎么样？", ticker="AAPL"), service.tools(DAY)
    )
    assert any(
        t.get("tool") == "get_provider_ttm_valuation" and t["result"]["found"] for t in answer.trace
    )
    assert answer.answer[0]["kind"] == "analysis"
    assert not any(
        e["kind"] == "valuation" or e.get("operation") == "provider_ttm_pe" for e in answer.evidence
    )
    assert not any("已计算供应商 TTM" in gap for gap in answer.gaps)
    assert (
        audit_package(
            create_package(answer, service.domain, "AAPL"), market_cache=service.market.cache
        )["checks_failed"]
        == 0
    )


@pytest.mark.parametrize("value", [0, -2])
def test_nonpositive_eps_keeps_eps_but_withholds_pe(service, value):
    service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=eps_body(value)))
    )
    answer = service.research(
        ResearchRequest(language="zh", question="TTM市盈率是多少？", ticker="AAPL", mode="reference")
    )
    assert answer.status == "partial" and [c["kind"] for c in answer.answer] == ["valuation"]
    assert "不适用" in " ".join(answer.gaps)
    assert service.audit_run(answer.id)["checks_failed"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "symbol",
        "metric",
        "annual",
        "currency",
        "nan",
        "boolean",
        "conflict",
        "future",
        "invalid_date",
    ],
)
def test_invalid_supplier_responses_are_not_facts(mutation):
    data = eps_body()
    series = data["timeseries"]["result"][0]
    row = series["trailingDilutedEPS"][0]
    if mutation == "symbol":
        series["meta"]["symbol"] = ["MSFT"]
    if mutation == "metric":
        series["meta"]["type"] = ["annualDilutedEPS"]
    if mutation == "annual":
        row["periodType"] = "12M"
    if mutation == "currency":
        row["currencyCode"] = "bad"
    if mutation == "nan":
        row["reportedValue"]["raw"] = "NaN"
    if mutation == "boolean":
        row["reportedValue"]["raw"] = True
    if mutation == "conflict":
        other = copy.deepcopy(row)
        other["reportedValue"]["raw"] = 5
        series["trailingDilutedEPS"].append(other)
    if mutation == "future":
        row["asOfDate"] = (DAY + timedelta(days=1)).isoformat()
    if mutation == "invalid_date":
        row["asOfDate"] = "bad"
    with pytest.raises(ValueError):
        parse(data)


def test_unsorted_rows_and_future_observations_select_latest_available():
    data = eps_body()
    rows = data["timeseries"]["result"][0]["trailingDilutedEPS"]
    rows.extend(
        [
            {**copy.deepcopy(rows[0]), "asOfDate": (DAY + timedelta(days=1)).isoformat()},
            {**copy.deepcopy(rows[0]), "asOfDate": (END - timedelta(days=90)).isoformat()},
        ]
    )
    assert parse(data).period_end_label == END


@pytest.mark.parametrize(
    "mutation",
    [
        "currency",
        "historical",
        "stale_eps",
        "stale_price",
        "old_eps",
        "split",
        "not_equity",
        "different_ticker",
        "different_day",
        "different_time",
    ],
)
def test_pe_rejects_incompatible_snapshots(service, mutation):
    tools = service.tools(DAY)
    tools.get_provider_ttm_valuation("AAPL")
    market = next(e for e in tools.ledger.values() if e["kind"] == "market")
    snapshot = parse()
    if mutation == "currency":
        snapshot = snapshot.model_copy(update={"currency": "HKD"})
    if mutation == "stale_eps":
        snapshot = snapshot.model_copy(update={"stale": True})
    if mutation == "old_eps":
        snapshot = snapshot.model_copy(update={"period_end_label": DAY - timedelta(days=201)})
    if mutation == "different_day":
        snapshot = snapshot.model_copy(update={"retrieved_at": NOW - timedelta(days=1)})
    if mutation == "different_time":
        snapshot = snapshot.model_copy(update={"retrieved_at": NOW - timedelta(minutes=31)})
    price = parse_chart(json.dumps(price_body()).encode(), "AAPL", "5Y", DAY, NOW, PRICE_URL)
    if mutation == "historical":
        price = price.model_copy(
            update={"quote_type": "historical_close", "quote_value": price.points[-1].close}
        )
    if mutation == "stale_price":
        price = price.model_copy(update={"stale": True})
    if mutation == "split":
        price = price.model_copy(update={"split_dates": [DAY - timedelta(days=1)]})
    if mutation == "not_equity":
        price = price.model_copy(update={"instrument_type": "ETF"})
    if mutation == "different_ticker":
        price = price.model_copy(update={"ticker": "MSFT"})
    market = market_evidence(price)
    eps = valuation_evidence(snapshot)
    tools.ledger[market["id"]], tools.ledger[eps["id"]] = market, eps
    with pytest.raises(ValueError):
        tools.calculate("provider_ttm_pe", market["id"], eps["id"])


def test_cache_failure_preserves_original_time_and_forbids_pe(service):
    original = service.market.valuations.eps("AAPL", DAY)
    service.market.valuations.now = lambda: NOW + timedelta(minutes=10)
    service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(502))
    )
    cached = service.market.valuations.eps("AAPL", DAY)
    assert (
        cached.stale
        and cached.retrieved_at == original.retrieved_at
        and cached.value == original.value
    )


@pytest.mark.parametrize("failed_source", ["eps", "price"])
def test_one_provider_failure_retains_the_other_source(service, failed_source):
    def handler(request):
        eps = "fundamentals-timeseries" in str(request.url)
        if eps == (failed_source == "eps"):
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json=eps_body() if eps else price_body())

    service.market.client = service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )
    answer = service.research(
        ResearchRequest(language="zh", question="TTM市盈率是多少？", ticker="AAPL", mode="reference")
    )
    assert answer.status == "partial" and answer.gaps
    assert [c["kind"] for c in answer.answer] == (
        ["market"] if failed_source == "eps" else ["valuation"]
    )
    assert service.audit_run(answer.id)["checks_failed"] == 0


def test_corrupt_cache_does_not_become_a_source(service):
    snapshot = service.market.valuations.eps("AAPL", DAY)
    (service.market.cache / "raw" / (snapshot.source_sha256 + ".json")).write_bytes(b"corrupt")
    service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(429))
    )
    with pytest.raises(ValueError):
        service.market.valuations.eps("AAPL", DAY)


def test_past_date_is_blocked_before_network_and_new_tool_in_mcp_plan(service):
    plan = service.research_plan(
        ResearchRequest(language="zh", question="TTM市盈率是多少？", ticker="AAPL", as_of=DAY - timedelta(days=1))
    )
    assert not plan["ready"] and "历史" in plan["message"]
    with pytest.raises(ValueError):
        service.market.valuations.eps("AAPL", DAY - timedelta(days=1))
    with TestClient(create_app(service)) as client:
        result = client.get(
            "/api/market/valuation", params={"ticker": "AAPL", "basis": "provider_ttm"}
        )
        assert result.status_code == 200 and result.json()["found"]
        assert [e["kind"] for e in result.json()["evidence"]["inputs"]] == ["market", "valuation"]
        assert (
            client.get(
                "/api/market/valuation", params={"ticker": "AAPL", "basis": "bad"}
            ).status_code
            == 422
        )


def test_changed_eps_resealed_package_fails_independent_raw_comparison(service):
    answer = service.research(
        ResearchRequest(language="zh", question="TTM EPS是多少？", ticker="AAPL", mode="reference")
    )
    snapshot = ValuationEvidence.model_validate(answer.evidence[0]).snapshot.model_copy(
        update={"value": Decimal("999")}
    )
    altered = valuation_evidence(snapshot)
    tools = service.tools(DAY)
    tools.ledger[altered["id"]] = altered
    claims, errors = verify_submission(
        Submission(claims=[Claim(kind="valuation", evidence_ids=[altered["id"]])]),
        tools,
        answer.question,
        focus_ticker="AAPL",
    )
    assert not errors
    changed = answer.model_copy(update={"answer": claims, "evidence": [altered]})
    package = create_package(changed, service.domain, "AAPL")
    assert package["manifest"]["payload_sha256"] == digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    audit = audit_package(package, market_cache=service.market.cache)
    assert audit["status"] == "invalid" and any(
        c["code"] == "raw_valuation" and c["status"] == "fail" for c in audit["checks"]
    )


def test_annual_question_cannot_be_answered_with_supplier_ttm(service):
    tools = service.tools(DAY)
    result = tools.get_provider_ttm_valuation("AAPL", False)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="valuation", evidence_ids=[result["evidence"]["id"]])]),
        tools,
        "AAPL年度每股收益是多少？",
        focus_ticker="AAPL",
    )
    assert not accepted and errors


def test_supplier_eps_can_support_qualified_agent_analysis_without_fake_filings(service):
    tools = service.tools(DAY)
    result = tools.get_provider_ttm_valuation("AAPL", False)
    reviewed = []

    def reviewer(question, claims):
        reviewed.extend(claims)
        return {
            "verdicts": [
                {
                    "index": 0,
                    "supported": True,
                    "reason": "Synthetic review of a qualified source limitation.",
                }
            ]
        }

    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    evidence_ids=[result["evidence"]["id"]],
                    statement="供应商的每股收益口径可作参考，仍需核对股数和同行基准才能评估价格。",
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        reviewer,
        "AAPL",
    )
    assert accepted and not errors
    assert reviewed[0]["evidence"][0]["filings"] == []
    assert "供应商" in reviewed[0]["evidence"][0]["text"]


def test_valuation_module_allows_only_typed_p_e_numbers_in_leading_analysis(service):
    add_annual_eps(service)
    tools = service.tools(DAY)
    result = tools.get_stock_valuation("AAPL")
    calculation = result["evidence"]
    value = Decimal(str(calculation["value"]))
    question = "What annual-basis and supplier trailing-basis valuation multiples are observed?"
    def reviewer(_question, claims):
        return {
            "verdicts": [
                {"index": claim["index"], "supported": True, "reason": "Controlled review"}
                for claim in claims
            ]
        }
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    evidence_ids=[calculation["id"]],
                    statement=f"年度市盈率为 {value:.2f} 倍；该倍数只是年度 EPS 口径参照。",
                )
            ]
        ),
        tools,
        question,
        reviewer,
        focus_ticker="AAPL",
    )
    assert accepted and not errors
    rejected, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    evidence_ids=[calculation["id"]],
                    statement="年度市盈率为 999.00 倍；该倍数只是年度 EPS 口径参照。",
                )
            ]
        ),
        tools,
        question,
        reviewer,
        focus_ticker="AAPL",
    )
    assert not rejected and any("typed annual/TTM P/E" in error for error in errors)


def test_same_symbol_concurrent_requests_share_a_cached_eps_response(service):
    from concurrent.futures import ThreadPoolExecutor

    calls = []
    service.market.valuations.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(str(request.url)) or httpx.Response(200, json=eps_body())
        )
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: service.market.valuations.eps("AAPL", DAY), range(4)))
    assert len(calls) == 1 and all(s == snapshots[0] for s in snapshots)
