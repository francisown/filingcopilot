from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from filingcopilot.audit import audit_package, create_package
from filingcopilot.market import (
    MarketData,
    MarketRouter,
    MarketSnapshot,
    PricePoint,
    SourceFailure,
    YahooSource,
    market_evidence,
    render_market,
)
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

DAY = date(2026, 10, 8)
RETRIEVED = datetime(2026, 10, 9, 1, tzinfo=timezone.utc)


def snapshot(ticker, *, source, currency="USD", close="100", zone="America/New_York"):
    at = int(datetime.combine(DAY, time(16), ZoneInfo(zone)).timestamp())
    return MarketSnapshot(
        ticker=ticker,
        name=ticker,
        currency=currency,
        exchange="TEST",
        exchange_timezone=zone,
        period="1M",
        as_of=DAY,
        retrieved_at=RETRIEVED,
        source_url="https://query1.finance.yahoo.com/v8/finance/chart/" + ticker,
        source_sha256=("a" if source == "Yahoo Finance" else "b") * 64,
        source_name=source,
        basis="close",
        points=[PricePoint(date=DAY, at=at, close=close)],
        quote_value=close,
        quote_at=at,
        quote_type="historical_close",
    )


@dataclass
class FixedSource:
    name: str
    result: object

    def fetch_daily(self, ticker, period, as_of, *, refresh=False):
        return self.result

    def fetch_quote(self, ticker, as_of, *, refresh=False):
        return self.result


def test_router_falls_back_when_primary_source_is_unavailable():
    primary = SourceFailure(
        "Yahoo Finance", "TSM", "connection", "连接失败", ConnectionError("fixture")
    )
    secondary = snapshot("TSM", source="Twelve Data", currency="USD")
    result = MarketRouter(
        FixedSource("Yahoo Finance", primary), FixedSource("Twelve Data", secondary)
    ).fetch_daily("TSM", "1M", DAY)
    assert result == secondary
    assert result.source_name == "Twelve Data"


def test_router_retains_conflicting_source_values_as_evidence():
    primary = snapshot("AAPL", source="Yahoo Finance", close="100")
    secondary = snapshot("AAPL", source="Twelve Data", close="110")
    result = MarketRouter(
        FixedSource("Yahoo Finance", primary), FixedSource("Twelve Data", secondary)
    ).fetch_daily("AAPL", "1M", DAY)
    assert result.source_conflicts[0].left_value == 100
    assert result.source_conflicts[0].right_value == 110
    evidence = market_evidence(result)
    assert evidence["snapshot"]["source_conflicts"][0]["right_source"] == "Twelve Data"
    assert "差异" in render_market(result)
    assert "不应" not in render_market(result)


@pytest.mark.parametrize("quote", ["100", "130"])
def test_daily_cross_check_compares_closes_instead_of_live_quote(quote):
    primary = snapshot("AAPL", source="Yahoo Finance", close="100").model_copy(
        update={"quote_value": Decimal(quote), "quote_type": "provider_quote"}
    )
    secondary = snapshot("AAPL", source="Twelve Data", close="100")

    result = MarketRouter._with_conflict(primary, secondary)

    assert result.source_conflicts == []
    assert result.quote_value == Decimal(quote)


def test_daily_conflict_values_bind_to_same_date_closes_and_keep_live_quote():
    primary = snapshot("AAPL", source="Yahoo Finance", close="100").model_copy(
        update={"quote_value": Decimal("110"), "quote_type": "provider_quote"}
    )
    secondary = snapshot("AAPL", source="Twelve Data", close="110")

    result = MarketRouter._with_conflict(primary, secondary)

    conflict = result.source_conflicts[0]
    assert conflict.trade_date == DAY
    assert conflict.left_value == 100 and conflict.right_value == 110
    assert conflict.relative_difference_pct == abs(Decimal(100) / 110 - 1) * 100
    assert result.quote_value == 110


@pytest.mark.parametrize("mismatch", ["ticker", "currency"])
def test_cross_check_identity_mismatch_is_visible_without_a_numeric_conflict(mismatch):
    primary = snapshot("AAPL", source="Yahoo Finance", close="100")
    secondary = snapshot(
        "MSFT" if mismatch == "ticker" else "AAPL",
        source="Twelve Data",
        currency="HKD" if mismatch == "currency" else "USD",
        close="900",
    )

    result = MarketRouter._with_conflict(primary, secondary)

    assert result.source_conflicts == []
    assert result.quote_value == 100 and result.currency == "USD"
    assert any("代码或币种" in warning for warning in result.warnings)


def test_quote_adapter_uses_actual_quote_day_when_daily_history_lags():
    primary = snapshot("AAPL", source="Yahoo Finance").model_copy(
        update={
            "as_of": date(2026, 10, 9),
            "retrieved_at": datetime(2026, 10, 9, 16, tzinfo=timezone.utc),
            "quote_type": "provider_quote",
            "quote_at": int(datetime(2026, 10, 9, 15, tzinfo=timezone.utc).timestamp()),
        }
    )
    router = MarketRouter(FixedSource("Yahoo Finance", primary))
    owner = type("Owner", (), {"_yahoo_history": lambda *args, **kwargs: primary})()

    for quote in (
        router.fetch_quote("AAPL", primary.as_of),
        YahooSource(owner).fetch_quote("AAPL", primary.as_of),
    ):
        assert quote.trade_date == date(2026, 10, 9)
        assert quote.at == primary.quote_at
    assert primary.points[-1].date == DAY


def test_router_supports_multiple_listing_timezones_without_currency_conversion():
    listings = [
        ("TSM", "USD", "America/New_York"),
        ("2330.TW", "TWD", "Asia/Taipei"),
        ("0700.HK", "HKD", "Asia/Hong_Kong"),
        ("600519.SS", "CNY", "Asia/Shanghai"),
    ]
    for ticker, currency, zone in listings:
        result = MarketRouter(
            FixedSource(
                "Yahoo Finance",
                snapshot(ticker, source="Yahoo Finance", currency=currency, zone=zone),
            )
        ).fetch_daily(ticker, "1M", DAY)
        assert result.ticker == ticker
        assert result.currency == currency
        assert result.points[-1].date == DAY


def test_conflict_snapshot_is_auditable_in_a_research_package(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    market = MarketData(tmp_path, now=lambda: RETRIEVED)
    market.router = MarketRouter(
        FixedSource("Yahoo Finance", snapshot("AAPL", source="Yahoo Finance", close="100")),
        FixedSource("Twelve Data", snapshot("AAPL", source="Twelve Data", close="110")),
    )
    service.market = market
    answer = service.research(
        ResearchRequest(question="苹果股价是多少？", ticker="AAPL", as_of=DAY, mode="reference")
    )
    assert answer.answer[0]["evidence_ids"]
    package = create_package(answer, service.domain, "AAPL")
    assert audit_package(package, market_cache=market.cache)["status"] == "consistent"
