import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, create_package, digest
from filingcopilot.market import MarketData, MarketError, parse_chart, statistics, symbol
from filingcopilot.models import Fact, Filing, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

NOW = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
URL = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?interval=1d"


def provider():
    stamps = [
        int(datetime(2026, 10, day, 20, tzinfo=timezone.utc).timestamp()) for day in (5, 6, 7)
    ]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "AAPL",
                        "currency": "USD",
                        "longName": "Apple Inc.",
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": 125,
                        "regularMarketTime": stamps[-1],
                    },
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{"close": [100, 80, 120], "volume": [1000, 2000, 3000]}],
                        "adjclose": [{"adjclose": [50, 40, 60]}],
                    },
                }
            ],
            "error": None,
        }
    }


def parse(data=None, as_of=date(2026, 10, 8)):
    return parse_chart(json.dumps(data or provider()).encode(), "AAPL", "MAX", as_of, NOW, URL)


def test_returns_and_drawdown_use_full_adjusted_series():
    result = statistics(parse())
    assert Decimal(result["change_pct"]) == 20
    assert Decimal(result["max_drawdown_pct"]) == -20
    assert result["quote_value"] == "125"
    assert result["trading_days"] == 3
    assert [result[key] for key in ("low", "p25", "median", "p75", "high")] == [
        "40",
        "40",
        "50",
        "60",
        "60",
    ]


def test_new_calendar_day_refreshes_even_when_cache_ttl_has_not_expired(tmp_path):
    clock, calls = [NOW], []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=provider())

    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: clock[0],
        ttl=200000,
    )
    previous = market.history("AAPL", "MAX", NOW.date())
    # A long test TTL deterministically crosses all CI host timezones. The same
    # boundary happens within a normal five-minute TTL at local midnight.
    clock[0] = NOW + timedelta(days=2)
    current = market.history("AAPL", "MAX", clock[0].date())
    assert len(calls) == 2
    assert current.retrieved_at == clock[0]
    assert previous.retrieved_at == NOW


def test_historical_cutoff_cannot_leak_current_quote():
    snapshot = parse(as_of=date(2026, 10, 6))
    assert snapshot.quote_type == "historical_close"
    assert snapshot.quote_value == 80
    assert [p.date.isoformat() for p in snapshot.points] == ["2026-10-05", "2026-10-06"]


def test_incomplete_adjustment_uses_consistent_unadjusted_basis():
    data = provider()
    data["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][1] = None
    snapshot = parse(data)
    assert snapshot.basis == "close"
    assert statistics(snapshot)["first"] == "100"


@pytest.mark.parametrize(
    "mutation", ["different_symbol", "duplicate_date", "html", "nan", "invalid_timezone"]
)
def test_bad_provider_data_is_not_presented_as_valid(mutation):
    data = provider()
    row = data["chart"]["result"][0]
    if mutation == "different_symbol":
        row["meta"]["symbol"] = "MSFT"
    elif mutation == "duplicate_date":
        row["timestamp"][1] = row["timestamp"][0]
    elif mutation == "nan":
        row["indicators"]["quote"][0]["close"] = [None, float("nan"), -1]
    elif mutation == "invalid_timezone":
        row["meta"]["exchangeTimezoneName"] = "Invalid/Zone"
    raw = b"<html>challenge</html>" if mutation == "html" else json.dumps(data).encode()
    with pytest.raises(MarketError):
        parse_chart(raw, "AAPL", "MAX", date(2026, 10, 8), NOW, URL)


@pytest.mark.parametrize("ticker", ["../secret", "AAPL/..", "AAPL?foo", "<script>"])
def test_symbol_cannot_escape_cache_or_url(ticker):
    with pytest.raises(MarketError):
        symbol(ticker)


def test_cache_deduplicates_requests_and_fallback_retains_source_time(tmp_path):
    calls = []
    clock = [NOW]
    fail = [False]

    def handler(request):
        calls.append(request.url)
        time.sleep(0.02)
        if fail[0]:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json=provider(), request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: clock[0]
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: market.history("AAPL", "MAX"), range(4)))
    assert len(calls) == 1 and all(r == results[0] for r in results)
    clock[0] = datetime(2026, 10, 9, 16, tzinfo=timezone.utc)
    fail[0] = True
    fallback = market.history("AAPL", "MAX")
    assert fallback.stale and fallback.retrieved_at == NOW
    assert len(calls) == 3  # Both provider hosts were tried before the saved response.
    assert (
        hashlib.sha256(
            (market.cache / "raw" / (fallback.source_sha256 + ".json")).read_bytes()
        ).hexdigest()
        == fallback.source_sha256
    )


def test_market_api_and_research_work_without_llm_and_are_auditable(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=provider(), request=r))
        ),
        now=lambda: NOW,
    )
    client = TestClient(create_app(service))
    data = client.get("/api/market/history?ticker=AAPL&period=MAX&as_of=2026-10-08").json()
    assert data["statistics"]["trading_days"] == 3
    answer = service.research(
        ResearchRequest(language="zh", question="苹果股价是多少？", ticker="AAPL", as_of=date(2026, 10, 8))
    )
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert answer.answer[0]["kind"] == "market"
    report = service.audit_run(answer.id)
    assert report["status"] == "consistent" and any(
        c["code"] == "raw_market" for c in report["checks"]
    )
    payload = copy.deepcopy(create_package(answer, service.domain, "AAPL"))
    payload["research"]["evidence"][0]["snapshot"]["points"][0]["close"] = "10000"
    payload["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in payload.items() if k != "manifest"}
    )
    report = audit_package(payload, market_cache=service.market.cache)
    assert (
        report["status"] == "invalid"
    )  # A rewritten checksum cannot defeat raw-source comparison.
    assert client.get("/api/market/history?ticker=../private").status_code == 422


def test_saved_market_numeric_text_tamper_is_rejected(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=provider(), request=r))
        ),
        now=lambda: NOW,
    )
    answer = service.research(
        ResearchRequest(language="zh", question="过去五年股价趋势图", ticker="AAPL", as_of=date(2026, 10, 8))
    )
    payload = create_package(answer, service.domain, "AAPL")
    payload["research"]["answer"][0]["text"] = "股价上涨 1000%。"
    payload["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in payload.items() if k != "manifest"}
    )
    assert audit_package(payload)["status"] == "invalid"


def eps_fixture(service, value="4", ticker="AAPL", unit="USD/shares"):
    filing = Filing(
        id="eps-filing",
        ticker=ticker,
        company="Test",
        cik="0000320193",
        accession="0000320193-26-000001",
        form="10-K",
        filed="2026-02-01",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/Archives/eps.htm",
        content_sha256="a" * 64,
    )
    fact = Fact(
        id="eps-2025",
        filing_id=filing.id,
        ticker=ticker,
        metric="diluted_eps",
        label="稀释每股收益",
        concept="us-gaap:EarningsPerShareDiluted",
        value=value,
        unit=unit,
        fiscal_year=2025,
        start="2025-01-01",
        end="2025-12-31",
        source_url="https://data.sec.gov/eps.json",
        locator="test",
    )
    service.corpus.ingest(filing, [fact], [])
    return fact


def test_annual_pe_is_source_bound_and_not_ttm(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=provider(), request=r))
        ),
        now=lambda: NOW,
    )
    eps_fixture(service)
    answer = service.research(
        ResearchRequest(language="zh", question="年度市盈率是多少？", ticker="AAPL", as_of=date(2026, 10, 8))
    )
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert "31.25 倍" in answer.answer[0]["text"] and "非TTM" in answer.answer[0]["text"]
    report = service.audit_run(answer.id)
    assert report["status"] == "consistent"
    assert any(c["code"] == "calculation" and c["status"] == "pass" for c in report["checks"])


@pytest.mark.parametrize("case", ["negative_eps", "currency", "post_filing_split", "historical"])
def test_annual_pe_rejects_invalid_or_mismatched_bases(tmp_path, case):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    data = provider()
    if case == "post_filing_split":
        data["chart"]["result"][0]["events"] = {
            "splits": {
                "split": {"date": data["chart"]["result"][0]["timestamp"][0], "splitRatio": "2:1"}
            }
        }
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data, request=r))
        ),
        now=lambda: NOW,
    )
    eps_fixture(
        service,
        value="-4" if case == "negative_eps" else "4",
        unit="EUR/shares" if case == "currency" else "USD/shares",
    )
    tools = service.tools(date(2026, 10, 6) if case == "historical" else date(2026, 10, 8))
    result = tools.get_stock_valuation("AAPL")
    assert not result["found"]
    assert not any(e["kind"] == "calculation" for e in tools.ledger.values())


def test_additive_registry_remains_compatible_with_older_packages(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="苹果 FY2025 营收是多少？",
            ticker="AAPL",
            mode="reference",
            as_of=date(2026, 10, 8),
        )
    )
    package = create_package(answer, service.domain, "AAPL")
    package["domain"]["metrics"].pop("diluted_eps")
    # This older registry also predates native issuer profiles referencing EPS.
    package["domain"].pop("financial_profiles")
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package, trusted_domain=service.domain)["status"] == "consistent"
    package["domain"]["metrics"]["revenue"]["label"] = "假收入"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package, trusted_domain=service.domain)["status"] == "invalid"


def test_capability_plan_prevents_unsupported_work_before_model_calls(tmp_path):
    class NoCalls:
        def complete(self, *args, **kwargs):
            raise AssertionError("Unsupported request must not consume a model call")

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=NoCalls())
    client = TestClient(create_app(service))
    request = {"question": "亚马逊股票明天股价会不会涨？", "ticker": "AAPL", "as_of": "2026-10-08"}
    plan = client.post("/api/research/plan", json=request).json()
    assert not plan["ready"] and plan["suggestions"]
    assert client.post("/api/research", json=request).status_code == 422
    request["question"] = "过去五年股价涨跌和最大回撤是多少？"
    assert client.post("/api/research/plan", json=request).json()["kind"] == "market"


def test_single_pe_cannot_establish_a_valuation_level(tmp_path):
    from filingcopilot.verification import analysis_conflict

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    assert analysis_conflict("目前市盈率不低，价格偏高。", [], service.domain)
    assert (
        analysis_conflict("不能仅凭年度市盈率判断价格便宜，尚需核验增长假设。", [], service.domain)
        is None
    )


def test_price_and_eps_wording_remains_auditable(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=provider(), request=r))
        ),
        now=lambda: NOW,
    )
    eps_fixture(service)
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="年度 EPS 口径市盈率是多少？", ticker="AAPL", as_of=date(2026, 10, 8)
        )
    )
    assert answer.status == "answered" and service.audit_run(answer.id)["status"] == "consistent"


def test_question_presets_follow_selected_market_scope(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    client = TestClient(create_app(service))
    supported = client.get("/api/capabilities", params={"ticker": "AAPL"}).json()
    foreign = client.get("/api/capabilities", params={"ticker": "0700.HK"}).json()
    assert "TTM 毛利率、营业利润率和净利率是多少？" in supported["questions"]
    assert len(foreign["questions"]) == 4
    assert any("HKEXnews" in question for question in foreign["questions"])
    assert any("SPY" in question for question in foreign["questions"])
    assert any("自由现金流" in question for question in supported["questions"])
    assert any("TTM" in question for question in supported["questions"])
    for question in foreign["questions"]:
        plan = client.post(
            "/api/research/plan", json={"question": question, "ticker": "0700.HK"}
        ).json()
        expected = "benchmark" if "SPY" in question else "updates" if "HKEXnews" in question else "market"
        assert plan["ready"] and plan["kind"] == expected


def test_explicit_refresh_bypasses_ttl_without_discarding_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        data = provider()
        data["chart"]["result"][0]["meta"]["regularMarketPrice"] = 120 + len(calls)
        return httpx.Response(200, json=data, request=request)

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    client = TestClient(create_app(service))
    params = {"ticker": "AAPL", "period": "MAX", "as_of": "2026-10-08"}
    first = client.get("/api/market/history", params=params).json()
    cached = client.get("/api/market/history", params=params).json()
    refreshed = client.get("/api/market/history", params={**params, "refresh": True}).json()
    assert cached["snapshot"] == first["snapshot"]
    assert len(calls) == 2
    assert refreshed["snapshot"]["quote_value"] != first["snapshot"]["quote_value"]
