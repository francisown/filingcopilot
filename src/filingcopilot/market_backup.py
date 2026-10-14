"""Independent daily-price adapter; credentials travel only in an HTTP header."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx

from .execution import bounded_timeout, cancellable_lock, checkpoint, progress
from .market import (
    PERIODS,
    MarketError,
    MarketSnapshot,
    PricePoint,
    _months_before,
    _retry_seconds,
    symbol,
)

SOURCE = "https://api.twelvedata.com/time_series"
NOTE = "Twelve Data 日线，按拆股调整，未按现金股息调整；原文仅给交易日期，未给精确报价时刻。仅使用已结束日期，不能作为实时行情或与 Yahoo EPS 混算市盈率。"


def backup_url(url):
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "api.twelvedata.com"
        and parsed.path == "/time_series"
        and not parsed.fragment
        and set(params) == {"symbol", "country", "interval", "adjust", "outputsize", "order"}
        and len(params["symbol"]) == 1
        and params["country"] == ["United States"]
        and params["interval"] == ["1day"]
        and params["adjust"] == ["splits"]
        and params["outputsize"] == ["5000"]
        and params["order"] == ["asc"]
    )


def parse_backup(raw, ticker, period, as_of, retrieved_at, url, *, stale=False):
    if not backup_url(url) or period not in PERIODS:
        raise MarketError("独立行情来源或区间无效。")
    ticker = symbol(ticker)
    requested = parse_qs(urlsplit(url).query)["symbol"][0]
    if symbol(requested) != ticker or not re.fullmatch(r"[A-Z]{1,5}(?:-[AB])?", ticker):
        raise MarketError("独立行情目前仅支持明确的美国普通股或 ETF 代码。")
    try:
        payload = json.loads(raw)
        meta, rows = payload["meta"], payload["values"]
        if symbol(meta["symbol"]) != ticker or meta["currency"] != "USD":
            raise ValueError("Wrong currency or listing")
        venues = {
            "NASDAQ": {"XNAS", "XNGS", "XNMS", "XNCM"},
            "NYSE": {"XNYS", "ARCX"},
            "NYSE ARCA": {"ARCX"},
            "NYSE MKT": {"XASE"},
            "AMEX": {"XASE"},
        }
        if meta["exchange"] not in venues or meta["mic_code"] not in venues[meta["exchange"]]:
            raise ValueError("Unknown US venue")
        if meta["exchange_timezone"] != "America/New_York" or meta["type"] not in (
            "Common Stock",
            "ETF",
        ):
            raise ValueError("Unknown share type or timezone")
        zone = ZoneInfo(meta["exchange_timezone"])
        cutoff = min(as_of, retrieved_at.astimezone(zone).date() - timedelta(days=1))
        if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
            raise ValueError("Invalid bar count")
        points, prior = [], None
        for row in rows:
            day = datetime.strptime(row["datetime"], "%Y-%m-%d").date()
            if row["datetime"] != day.isoformat() or prior and day <= prior:
                raise ValueError("Unordered or duplicate dates")
            prior = day
            value = Decimal(row["close"])
            if not value.is_finite() or value <= 0:
                raise ValueError("Invalid closing value")
            if day <= cutoff:
                volume = Decimal(str(row["volume"])) if row.get("volume") is not None else None
                if volume is not None and (
                    not volume.is_finite() or volume < 0 or volume != volume.to_integral_value()
                ):
                    raise ValueError("Invalid share volume")
                points.append(
                    PricePoint(
                        date=day,
                        at=int(datetime.combine(day, datetime.min.time(), zone).timestamp()),
                        close=value,
                        volume=int(volume) if volume is not None else None,
                    )
                )
        if not points:
            raise ValueError("No completed daily bars before knowledge date")
        last = points[-1]
        months = PERIODS[period]
        start = _months_before(as_of, months) if months else None
        selected = [p for p in points if start is None or p.date >= start]
        if not selected:
            raise ValueError("No data in requested interval")
        warnings = [NOTE]
        if len(rows) == 5000:
            warnings.append(
                "此响应至多 5000 个交易日；全部历史仅表示当前来源返回范围，不保证覆盖上市以来。"
            )
        if (cutoff - last.date).days > 7:
            warnings.append("来源最近日线距抓取日期超过一周，请检查停牌、市场假日或数据更新。")
        return MarketSnapshot(
            ticker=ticker,
            name=ticker,
            currency="USD",
            exchange=meta["exchange"],
            instrument_type="ETF" if meta["type"] == "ETF" else "EQUITY",
            exchange_timezone=meta["exchange_timezone"],
            period=period,
            as_of=as_of,
            retrieved_at=retrieved_at,
            source_url=url,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            source_name="Twelve Data",
            basis="split_adjusted_close",
            points=selected,
            quote_value=last.close,
            quote_at=last.at,
            quote_type="historical_close",
            stale=stale,
            warnings=warnings,
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise MarketError("独立行情响应不完整或证券、币种、日期未匹配；保留原行情状态。") from exc


class IndependentMarket:
    def __init__(self, primary, api_key):
        self.primary, self.api_key = primary, api_key
        self.lock = threading.Lock()
        self.symbol_locks = {}
        self.retry_at = None

    def history(self, ticker, period, as_of):
        ticker = symbol(ticker)
        if not re.fullmatch(r"[A-Z]{1,5}(?:-[AB])?", ticker):
            raise MarketError("独立备用源目前覆盖美国普通股和 ETF，其他市场保留原行情来源。")
        key = "backup-" + ticker
        with self.lock:
            ticker_lock = self.symbol_locks.setdefault(ticker, threading.Lock())
        with cancellable_lock(ticker_lock):
            now = self.primary.now()
            cached = self.primary._read(key)
            with self.lock:
                retry_at = self.retry_at
            if retry_at and now < retry_at:
                if cached:
                    return parse_backup(
                        cached[0],
                        ticker,
                        period,
                        min(as_of, cached[2].date()),
                        cached[2],
                        cached[1],
                        stale=True,
                    )
                raise MarketError("独立备用源冷却中；稍后重试，主来源故障状态仍保留。")
            if cached and 0 <= (now - cached[2]).total_seconds() < self.primary.ttl:
                return parse_backup(cached[0], ticker, period, as_of, cached[2], cached[1])
            url = (
                SOURCE
                + "?"
                + urlencode(
                    {
                        "symbol": ticker.replace("-", "."),
                        "country": "United States",
                        "interval": "1day",
                        "adjust": "splits",
                        "outputsize": 5000,
                        "order": "asc",
                    }
                )
            )
            try:
                checkpoint()
                progress("independent_market", "主来源未完成，正在读取独立供应商已结束日线。")
                response = self.primary.client.get(
                    url,
                    headers={"Authorization": "apikey " + self.api_key},
                    timeout=httpx.Timeout(bounded_timeout(12), connect=bounded_timeout(5)),
                )
                response.raise_for_status()
                if len(response.content) > 10_000_000:
                    raise MarketError("独立行情响应过大。")
                parsed = parse_backup(
                    response.content, ticker, period, as_of, self.primary.now(), url
                )
                self.primary._save(key, response.content, url, parsed.retrieved_at)
                with self.lock:
                    if self.retry_at and self.primary.now() >= self.retry_at:
                        self.retry_at = None
                return parsed
            except (httpx.HTTPError, MarketError) as exc:
                headers = exc.response.headers if isinstance(exc, httpx.HTTPStatusError) else {}
                wait = _retry_seconds(headers, self.primary.now(), 60)
                with self.lock:
                    until = self.primary.now() + timedelta(seconds=wait)
                    self.retry_at = max(self.retry_at, until) if self.retry_at else until
                if cached:
                    return parse_backup(
                        cached[0],
                        ticker,
                        period,
                        min(as_of, cached[2].date()),
                        cached[2],
                        cached[1],
                        stale=True,
                    )
                raise MarketError(
                    "独立备用源未完成；请检查备用源密钥、套餐覆盖或稍后重试。"
                ) from None
