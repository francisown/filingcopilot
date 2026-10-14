from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from test_comparison_valuation import provider_service as provider_service
from test_valuation import DAY, add_annual_eps

from filingcopilot.api import create_app
from filingcopilot.historical_valuation import (
    HistoricalValuationRequest,
    historical_reference,
    validate_historical_reference,
)
from filingcopilot.market import MarketSnapshot, PricePoint, market_evidence
from filingcopilot.models import ResearchRequest

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

ZONE = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def fixture_clock(fixed_fixture_date):
    fixed_fixture_date(DAY)


@pytest.mark.parametrize("offset", [-1, 1])
def test_fixture_clock_keeps_current_day_validation(offset):
    assert ResearchRequest(question="Apple valuation").as_of == DAY
    assert HistoricalValuationRequest(ticker="AAPL").as_of == DAY
    with pytest.raises(ValueError, match="今天"):
        HistoricalValuationRequest(ticker="AAPL", as_of=DAY + timedelta(days=offset))


def _market():
    points = []
    for offset, close in ((3, "100"), (2, "110"), (1, "120")):
        day = DAY - timedelta(days=offset)
        at = int(datetime.combine(day, time(16), ZONE).timestamp())
        points.append(PricePoint(date=day, at=at, close=Decimal(close), adjusted=Decimal(close)))
    snapshot = MarketSnapshot(
        ticker="AAPL",
        name="Apple",
        currency="USD",
        exchange="NASDAQ",
        exchange_timezone="America/New_York",
        period="1Y",
        as_of=DAY,
        retrieved_at=datetime.combine(DAY, time(21), ZoneInfo("UTC")),
        source_url="https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        source_sha256="0" * 64,
        basis="adjusted_close",
        points=points,
        quote_value=Decimal("120"),
        quote_at=points[-1].at,
        quote_type="historical_close",
    )
    return market_evidence(snapshot)


def _eps(value="5"):
    return {
        "id": "fact-aapl-eps",
        "kind": "fact",
        "ticker": "AAPL",
        "metric": "diluted_eps",
        "value": value,
        "unit": "USD/shares",
        "start": "2024-01-01",
        "end": "2024-12-31",
        "filing": {"form": "10-K", "filed": "2025-05-01"},
    }


def test_historical_reference_is_public_date_bound_and_replayable():
    request = HistoricalValuationRequest(ticker="AAPL", period="1Y", as_of=DAY)
    market = _market()
    result = historical_reference(request, market, [_eps()])
    assert result["valid_days"] == 3
    assert result["statistics"]["median"] == "22"
    ledger = {market["id"]: market, "fact-aapl-eps": _eps()}
    validate_historical_reference(result, ledger, DAY)


@pytest.mark.parametrize("mutation", ["currency", "split", "stale"])
def test_historical_reference_retains_reasons_for_unmatched_points(mutation):
    request = HistoricalValuationRequest(ticker="AAPL", period="1Y", as_of=DAY)
    market = _market()
    eps = _eps()
    if mutation == "currency":
        eps["unit"] = "EUR/shares"
    if mutation == "split":
        market["snapshot"]["split_dates"] = [date.fromisoformat("2025-06-01").isoformat()]
    if mutation == "stale":
        eps["filing"]["filed"] = "2023-01-01"
    result = historical_reference(request, market, [eps])
    assert not result["valid_days"]
    assert result["missing_counts"]


def test_historical_reference_rejects_mixed_eps_inputs():
    request = HistoricalValuationRequest(ticker="AAPL", period="1Y", as_of=DAY)
    with pytest.raises(ValueError, match="annual diluted EPS"):
        historical_reference(request, _market(), [deepcopy(_eps()) | {"metric": "revenue"}])


def test_research_question_and_saved_package_include_historical_step(provider_service):
    service, _ = provider_service
    add_annual_eps(service)
    answer = service.research(
        ResearchRequest(question="苹果过去一年历史市盈率区间", ticker="AAPL", as_of=DAY)
    )
    assert answer.trace[0]["stage"] == "historical_valuation"
    assert answer.mode == "market"
    assert service.audit_run(answer.id)["status"] == "consistent"


def test_historical_valuation_api_returns_bound_evidence(provider_service):
    service, _ = provider_service
    add_annual_eps(service)
    from fastapi.testclient import TestClient

    response = TestClient(create_app(service)).get(
        "/api/market/historical-valuation?ticker=AAPL&period=1Y"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "historical_valuation"
    assert body["observed_evidence"]
