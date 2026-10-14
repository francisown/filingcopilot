"""Requests sharing one failed market source should not repeat the same wait."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, create_package
from filingcopilot.market import MarketData, MarketError, MarketUnavailable
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

NOW = datetime(2026, 10, 10, 3, 25, tzinfo=timezone.utc)


def chart(ticker="AAPL"):
    stamps = [
        int(datetime(2026, 10, day, 20, tzinfo=timezone.utc).timestamp()) for day in (7, 8, 9)
    ]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": ticker,
                        "currency": "USD",
                        "longName": ticker,
                        "exchangeTimezoneName": "America/New_York",
                        "exchangeName": "NASDAQ",
                        "regularMarketPrice": 102,
                        "regularMarketTime": stamps[-1],
                    },
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{"close": [100, 101, 102]}],
                        "adjclose": [{"adjclose": [99, 100, 101]}],
                    },
                }
            ],
            "error": None,
        }
    }


def test_concurrent_queries_share_failed_attempt_instead_of_repeating_wait(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(503, request=request)

    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )

    def query(period):
        try:
            market.history("AAPL", period, NOW.date())
        except MarketError as exc:
            return str(exc)
        raise AssertionError("An unavailable source must not manufacture prices")

    with ThreadPoolExecutor(max_workers=3) as pool:
        errors = list(pool.map(query, ("1Y", "5Y", "MAX")))
    assert all(errors)
    assert len(calls) == 5, "Three same-symbol requests should share Yahoo failure state and use bounded Nasdaq fallback"
    assert sum("api.nasdaq.com" in str(url) for url in calls) == 3


def test_network_failure_is_service_unavailable_not_invalid_stock_input(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(503, request=r))
        ),
        now=lambda: NOW,
    )
    response = TestClient(create_app(service)).get(
        "/api/market/history?ticker=AAPL&period=1Y&as_of=2026-10-10"
    )
    assert response.status_code == 503, response.text
    assert response.headers["retry-after"]
    assert response.json()["market_status"]["cause"] == "upstream"


def test_chart_rate_limit_also_pauses_ttm_supplier_requests(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429, headers={"Retry-After": "120"}, request=request)

    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )
    with pytest.raises(MarketUnavailable):
        market.history("AAPL")
    with pytest.raises(ValueError, match="120 秒"):
        market.valuations.eps("MSFT", NOW.date())
    assert len(calls) == 1


def test_ttm_rate_limit_pauses_price_requests_without_second_hostname_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429, headers={"Retry-After": "120"}, request=request)

    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )
    with pytest.raises(ValueError):
        market.valuations.eps("AAPL", NOW.date())
    assert len(calls) == 1
    with pytest.raises(MarketUnavailable, match="120 秒"):
        market.history("MSFT", refresh=True)
    assert len(calls) == 1


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout])
def test_connection_errors_have_no_supplier_or_proxy_details(tmp_path, exception):
    def handler(request):
        raise exception("private-proxy-password-never-display", request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    with pytest.raises(MarketUnavailable) as error:
        market.history("AAPL")
    assert error.value.status["cause"] == "connection"
    assert "private-proxy" not in str(error.value)
    assert "无需先下载财报" in str(error.value)


def test_expiry_and_explicit_retry_recover_without_poisoning_cache(tmp_path):
    calls, clock, failing = [], [NOW], [True]

    def handler(request):
        calls.append(request.url)
        return httpx.Response(503 if failing[0] else 200, json=chart(), request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: clock[0]
    )
    with pytest.raises(MarketUnavailable):
        market.history("AAPL")
    assert not market.cache.exists()
    failing[0] = False
    with pytest.raises(MarketUnavailable):
        market.history("AAPL")
    assert len(calls) == 4
    recovered = market.history("AAPL", refresh=True)
    assert not recovered.stale and len(calls) == 5
    assert market.availability("AAPL") is None
    failing[0] = True
    clock[0] += timedelta(minutes=6)
    stale = market.history("AAPL")
    assert stale.stale and stale.retrieved_at == NOW
    assert len(calls) == 7
    failing[0] = False
    clock[0] += timedelta(seconds=31)
    recovered = market.history("AAPL")
    assert not recovered.stale and recovered.retrieved_at == clock[0]
    assert len(calls) == 8 and market.availability("AAPL") is None


@pytest.mark.parametrize(
    "header", ["12", format_datetime(NOW + timedelta(seconds=12), usegmt=True)]
)
def test_supplier_retry_after_pauses_other_symbols_and_manual_refresh(tmp_path, header):
    calls, clock = [], [NOW]

    def handler(request):
        calls.append(request.url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": header}, request=request)
        ticker = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=chart(ticker), request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: clock[0]
    )
    with pytest.raises(MarketUnavailable) as error:
        market.history("AAPL")
    assert error.value.retry_after == 12 and len(calls) == 1
    clock[0] += timedelta(seconds=3)
    with pytest.raises(MarketUnavailable) as other:
        market.history("MSFT", refresh=True)
    assert other.value.retry_after == 9 and other.value.status["scope"] == "provider"
    assert len(calls) == 1
    clock[0] += timedelta(seconds=9)
    assert market.history("MSFT").ticker == "MSFT"
    assert len(calls) == 2 and market.provider_failure is None


@pytest.mark.parametrize("header", ["broken", "-1", "nan", "inf", "1e99"])
def test_invalid_retry_headers_use_bounded_default(tmp_path, header):
    market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(429, headers={"Retry-After": header}, request=r)
            )
        ),
        now=lambda: NOW,
    )
    with pytest.raises(MarketUnavailable) as error:
        market.history("AAPL")
    assert error.value.retry_after == 30


def test_local_outage_does_not_stop_an_unrelated_symbol(tmp_path):
    def handler(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(503 if ticker == "AAPL" else 200, json=chart(ticker), request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    with pytest.raises(MarketUnavailable):
        market.history("AAPL")
    assert market.history("MSFT").ticker == "MSFT"
    assert market.availability("MSFT") is None


def test_active_provider_pause_wins_over_a_longer_local_failure(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        if request.url.path.endswith("AAPL"):
            return httpx.Response(503, request=request)
        return httpx.Response(429, headers={"Retry-After": "5"}, request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    for ticker in ("AAPL", "MSFT"):
        with pytest.raises(MarketUnavailable):
            market.history(ticker)
    with pytest.raises(MarketUnavailable) as error:
        market.history("AAPL", refresh=True)
    assert len(calls) == 4 and error.value.status["cause"] == "rate_limited"
    assert error.value.retry_after == 5


def test_cached_api_reports_pause_without_rewriting_frozen_prices(tmp_path):
    clock, limited = [NOW], [False]

    def handler(request):
        if limited[0]:
            return httpx.Response(429, headers={"Retry-After": "45"}, request=request)
        return httpx.Response(200, json=chart(), request=request)

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: clock[0]
    )
    initial = service.market.history("AAPL")
    original_raw = (service.market.cache / "raw" / (initial.source_sha256 + ".json")).read_bytes()
    limited[0] = True
    clock[0] += timedelta(seconds=10)
    client = TestClient(create_app(service))
    response = client.get("/api/market/history?ticker=AAPL&period=1Y&as_of=2026-10-10&refresh=true")
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot"]["stale"] and data["availability"]["cause"] == "rate_limited"
    assert data["snapshot"]["retrieved_at"] == initial.model_dump(mode="json")["retrieved_at"]
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="苹果近一年股价走势", ticker="AAPL", as_of=NOW.date(), mode="reference"
        )
    )
    report = audit_package(
        create_package(answer, service.domain, "AAPL"), market_cache=service.market.cache
    )
    assert report["status"] == "consistent"
    assert (
        original_raw
        == (service.market.cache / "raw" / (initial.source_sha256 + ".json")).read_bytes()
    )


@pytest.mark.parametrize(
    "ticker,period,as_of",
    [
        ("../secret", "5Y", NOW.date()),
        ("AAPL", "wrong", NOW.date()),
        ("AAPL", "5Y", NOW.date() + timedelta(days=1)),
    ],
)
def test_invalid_input_still_returns_422_without_fetching(tmp_path, ticker, period, as_of):
    calls = []
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))),
        now=lambda: NOW,
    )
    response = TestClient(create_app(service)).get(
        "/api/market/history",
        params={"ticker": ticker, "period": period, "as_of": as_of.isoformat()},
    )
    assert response.status_code == 422 and not calls


@pytest.mark.parametrize("payload", [b"upstream returned HTML", b"{}", b"[]"])
def test_unreadable_success_response_is_an_upstream_failure(tmp_path, payload):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, content=payload, request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    for _ in range(2):
        with pytest.raises(MarketUnavailable) as error:
            market.history("AAPL")
        assert error.value.status["cause"] == "upstream"
    assert len(calls) == 4 and not market.cache.exists()


def test_wrong_symbol_response_cannot_poison_price_cache(tmp_path):
    market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=chart("MSFT"), request=r)
            )
        ),
        now=lambda: NOW,
    )
    with pytest.raises(MarketUnavailable):
        market.history("AAPL")
    assert not market.cache.exists()


@pytest.mark.parametrize("missing_symbol", [False, True])
def test_valid_no_data_result_does_not_create_a_provider_pause(tmp_path, missing_symbol):
    calls = []
    payload = (
        {"chart": {"result": None, "error": {"code": "Not Found", "description": "do-not-display"}}}
        if missing_symbol
        else chart()
    )

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json=payload, request=request)

    market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    with pytest.raises(MarketError) as error:
        market.history("AAPL", as_of=datetime(2010, 1, 1).date())
    assert not isinstance(error.value, MarketUnavailable)
    assert not market.failures and market.provider_failure is None
    assert "do-not-display" not in str(error.value)
    assert len(calls) == 2


def test_supplier_explicit_http_404_not_found_is_not_a_transient_outage(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    payload = {
        "chart": {
            "result": None,
            "error": {"code": "Not Found", "description": "supplier-detail-do-not-display"},
        }
    }
    service.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(404, json=payload, request=r))
        ),
        now=lambda: NOW,
    )
    response = TestClient(create_app(service)).get("/api/market/history?ticker=UNKNOWN")
    assert response.status_code == 422
    assert "verify the listing market" in response.json()["detail"]
    assert "supplier-detail" not in response.text
    assert service.market.availability("UNKNOWN") is None
