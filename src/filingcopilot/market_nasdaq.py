"""Public Nasdaq historical daily fallback for US-listed securities.

This adapter is deliberately historical-close only.  It never supplies an
intraday quote or a dividend-adjusted curve, and it is used only after the
primary Yahoo adapter cannot return a series.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from .market import PERIODS, MarketError, MarketSnapshot, PricePoint, _months_before, symbol

SOURCE = "https://api.nasdaq.com/api/quote"
ZONE = ZoneInfo("America/New_York")
NOTE = "Nasdaq 公开历史收盘价备用源；仅使用已结束交易日，不做分红复权，也不提供盘中报价。"


def nasdaq_url(url: str) -> bool:
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "api.nasdaq.com"
        and bool(re.fullmatch(r"/api/quote/[A-Z0-9^][A-Z0-9.^=-]{0,14}/historical", parsed.path))
        and params.get("assetclass") == ["stocks"]
        and len(params.get("fromdate", [])) == 1
        and len(params.get("todate", [])) == 1
        and params.get("limit") == ["5000"]
        and params.get("offset") == ["0"]
        and not parsed.fragment
    )


def _money(value) -> Decimal:
    cleaned = str(value or "").replace("$", "").replace(",", "").strip()
    number = Decimal(cleaned)
    if not number.is_finite() or number <= 0:
        raise ValueError("Invalid Nasdaq close")
    return number


def parse_nasdaq(raw, ticker, period, as_of, retrieved_at, url, *, stale=False):
    if period not in PERIODS or not nasdaq_url(url):
        raise MarketError("Nasdaq fallback URL or period is invalid.")
    ticker = symbol(ticker)
    path_symbol = unquote(urlsplit(url).path.split("/")[3]).upper()
    if path_symbol != ticker or not re.fullmatch(r"[A-Z]{1,5}(?:-[AB])?", ticker):
        raise MarketError("Nasdaq fallback currently covers US common stocks and ETFs only.")
    try:
        body = json.loads(raw)
        data = body["data"]
        rows = data["tradesTable"]["rows"]
        if data.get("symbol", ticker).upper() != ticker or not isinstance(rows, list):
            raise ValueError("Wrong Nasdaq symbol or rows")
        if not 1 <= len(rows) <= 5000:
            raise ValueError("Invalid Nasdaq row count")
        effective_date = min(as_of, retrieved_at.astimezone(ZONE).date())
        cutoff = _months_before(effective_date, PERIODS[period]) if PERIODS[period] else date.min
        points = []
        seen = set()
        for row in rows:
            day = datetime.strptime(str(row["date"]), "%m/%d/%Y").date()
            if day in seen or day > effective_date or day < cutoff:
                continue
            seen.add(day)
            close = _money(row["close"])
            volume = row.get("volume")
            volume_value = int(str(volume).replace(",", "")) if volume not in (None, "") else None
            if volume_value is not None and volume_value < 0:
                raise ValueError("Invalid Nasdaq volume")
            at = int(datetime.combine(day, time(16), ZONE).timestamp())
            points.append(PricePoint(date=day, at=at, close=close, volume=volume_value))
        points.sort(key=lambda point: point.at)
        if not points:
            raise MarketError("Nasdaq returned no completed trading day in the requested period.")
        warnings = [NOTE]
        if int(data.get("totalRecords") or len(rows)) >= 5000:
            warnings.append("Nasdaq 最多返回 5,000 行；全部历史不保证从上市首日开始。")
        if stale:
            warnings.append("The network request did not complete; this is a saved Nasdaq response.")
        last = points[-1]
        return MarketSnapshot(
            ticker=ticker,
            name=ticker,
            currency="USD",
            exchange="NASDAQ",
            instrument_type="EQUITY",
            exchange_timezone="America/New_York",
            period=period,
            as_of=effective_date,
            retrieved_at=retrieved_at,
            source_url=url,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            source_name="Nasdaq",
            basis="close",
            points=points,
            quote_value=last.close,
            quote_at=last.at,
            quote_type="historical_close",
            stale=stale,
            warnings=warnings,
        )
    except MarketError:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError, json.JSONDecodeError) as exc:
        raise MarketError("Nasdaq fallback response was incomplete or did not match the security.") from exc


class NasdaqSource:
    """Source adapter used only after the configured primary/backup sources."""

    name = "Nasdaq"

    def __init__(self, owner):
        self.owner = owner

    def fetch_daily(self, ticker, period, as_of, *, refresh=False):
        try:
            return self.owner._nasdaq_history(ticker, period, as_of, refresh=refresh)
        except (MarketError, httpx.HTTPError) as exc:
            from .market import _source_failure

            return _source_failure(self.name, ticker, exc)

    def fetch_quote(self, ticker, as_of, *, refresh=False):
        result = self.fetch_daily(ticker, "1M", as_of, refresh=refresh)
        if hasattr(result, "source_name"):
            from .market import MarketQuote

            return MarketQuote.from_snapshot(result)
        return result

