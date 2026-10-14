import copy
import json
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import httpx
import pytest

from filingcopilot.comparison import rebased_chart
from filingcopilot.market import (
    MarketData,
    MarketError,
    MarketUnavailable,
    market_evidence,
    parse_market,
)
from filingcopilot.market_backup import SOURCE, parse_backup

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
URL = (
    SOURCE
    + "?"
    + urlencode(
        {
            "symbol": "AAPL",
            "country": "United States",
            "interval": "1day",
            "adjust": "splits",
            "outputsize": 5000,
            "order": "asc",
        }
    )
)


def body():
    return {
        "meta": {
            "symbol": "AAPL",
            "currency": "USD",
            "exchange": "NASDAQ",
            "mic_code": "XNGS",
            "exchange_timezone": "America/New_York",
            "type": "Common Stock",
        },
        "values": [
            {"datetime": "2026-10-08", "close": "200.01", "volume": "1000"},
            {"datetime": "2026-10-09", "close": "204.02", "volume": "2000"},
            {"datetime": "2026-10-10", "close": "999.99", "volume": "20"},
        ],
    }


def test_source_decode_dates_split_basis_and_identity():
    raw = json.dumps(body()).encode()
    s = parse_backup(raw, "AAPL", "1Y", date(2026, 10, 10), NOW, URL)
    assert str(s.quote_value) == "204.02"
    assert s.points[-1].date == date(2026, 10, 9)
    assert s.quote_type == "historical_close" and s.basis == "split_adjusted_close"
    assert parse_market(raw, "AAPL", "1Y", s.as_of, NOW, URL) == s
    with pytest.raises(MarketError):
        parse_backup(raw, "MSFT", "1Y", s.as_of, NOW, URL)
    with pytest.raises(MarketError):
        parse_backup(raw, "AAPL", "1Y", s.as_of, NOW, URL + "&apikey=secret")


@pytest.mark.parametrize("mutation", ["currency", "mic", "adr", "duplicate", "negative", "nan"])
def test_bad_vendor_values_fail_closed(mutation):
    data = body()
    if mutation == "currency":
        data["meta"]["currency"] = "CAD"
    if mutation == "mic":
        data["meta"]["mic_code"] = "XLON"
    if mutation == "adr":
        data["meta"]["type"] = "Depositary Receipt"
    if mutation == "duplicate":
        data["values"][1]["datetime"] = "2026-10-08"
    if mutation == "negative":
        data["values"][0]["close"] = "-1"
    if mutation == "nan":
        data["values"][0]["close"] = "NaN"
    with pytest.raises(MarketError):
        parse_backup(json.dumps(data).encode(), "AAPL", "5Y", date(2026, 10, 10), NOW, URL)


def test_yahoo_429_uses_independent_header_only_and_reuses_cache(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "api.twelvedata.com":
            assert request.headers["authorization"] == "apikey private-fixture-key"
            assert "private-fixture-key" not in str(request.url)
            return httpx.Response(200, json=body())
        assert "authorization" not in request.headers
        return httpx.Response(429, headers={"Retry-After": "180"})

    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        now=lambda: NOW,
        backup_key="private-fixture-key",
    )
    s = market.history("AAPL", "5Y")
    assert s.basis == "split_adjusted_close" and not s.stale
    assert market.availability("AAPL")["provider"] == "Yahoo Finance"
    market.history("AAPL", "1Y", refresh=True)
    assert len(calls) == 2
    assert "private-fixture-key" not in json.dumps(market_evidence(s))
    assert not any("private-fixture-key" in p.read_text() for p in market.cache.rglob("*.json"))


def test_without_backup_key_keeps_existing_outage_semantics(tmp_path):
    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429))),
        now=lambda: NOW,
    )
    with pytest.raises(MarketUnavailable):
        market.history("AAPL")


def test_source_status_exposes_fallback_readiness_without_credentials(tmp_path):
    primary_only = MarketData(tmp_path)
    status = primary_only.source_status()

    assert status["primary"]["configured"] is True
    assert status["independent_fallback_configured"] is True
    assert status["fallbacks"][0]["name"] == "Twelve Data"
    assert status["fallbacks"][0]["configured"] is False
    assert status["fallbacks"][1]["name"] == "Nasdaq"
    assert status["fallbacks"][1]["configured"] is True
    assert "api" not in json.dumps(status).lower()

    independent = MarketData(tmp_path / "independent", backup_key="private-fixture-key")
    assert independent.source_status()["independent_fallback_configured"] is True


def test_invalid_range_and_future_date_do_not_call_either_supplier(tmp_path):
    calls = []
    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))),
        now=lambda: NOW,
        backup_key="private-fixture-key",
    )
    for period, cutoff in (("bad", date(2026, 10, 10)), ("1Y", date(2026, 10, 11))):
        with pytest.raises(MarketError, match="周期无效"):
            market.history("AAPL", period, cutoff)
    assert not calls


def test_incompatible_sources_do_not_make_a_rebased_comparison():
    s = parse_backup(json.dumps(body()).encode(), "AAPL", "1Y", date(2026, 10, 10), NOW, URL)
    left = market_evidence(s)
    right = copy.deepcopy(left)
    right["snapshot"]["source_url"] = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
    right["snapshot"]["basis"] = "close"
    right["id"] = market_evidence(type(s).model_validate(right["snapshot"]))["id"]
    # IDs bind raw/scope rather than vendor URL, so use a distinct raw hash for this fixture.
    right["snapshot"]["source_sha256"] = "c" * 64
    right["id"] = market_evidence(type(s).model_validate(right["snapshot"]))["id"]
    result = rebased_chart([left["id"], right["id"]], {left["id"]: left, right["id"]: right})
    assert not result["found"] and "口径" in result["reason"]
