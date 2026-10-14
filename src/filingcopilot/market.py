"""Public market data, date-safe price series and reproducible statistics.

The provider adapter is isolated here. No provider text enters model instructions.
Prices are not a real-time feed; a saved response remains independently auditable.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import httpx
from pydantic import Field, model_validator

from .execution import bounded_timeout, cancellable_lock, checkpoint, progress
from .models import StrictModel
from .stock_directory import StockDirectory

PERIODS = {"1M": 1, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60, "10Y": 120, "MAX": None}
UTC = timezone.utc


class MarketError(ValueError):
    pass


class _MarketResponseError(MarketError):
    pass


class MarketUnavailable(MarketError):
    """An upstream outage is distinct from an invalid research input."""

    def __init__(self, ticker: str, failure: "_MarketFailure", now: datetime):
        wait = max(1, math.ceil((failure.retry_at - now).total_seconds()))
        labels = {
            "connection": "行情源连接暂未完成",
            "upstream": "行情源暂未返回可用数据",
            "rate_limited": "行情供应商暂时限流",
        }
        self.retry_after = wait
        self.status = {
            "state": "source_unavailable",
            "provider": "Yahoo Finance",
            "cause": failure.cause,
            "scope": failure.scope,
            "retry_after": wait,
            "retry_at": failure.retry_at.isoformat(),
        }
        message = f"{ticker}：{labels[failure.cause]}。约 {wait} 秒后重试行情；无需先下载财报。"
        if failure.cause != "rate_limited":
            message += "也可点击刷新立即重试。"
        super().__init__(message)


@dataclass(frozen=True)
class _MarketFailure:
    cause: Literal["connection", "upstream", "rate_limited"]
    scope: Literal["symbol", "provider"]
    retry_at: datetime


def _retry_seconds(headers, now: datetime, default: float) -> float:
    value = headers.get("retry-after")
    if value is None:
        return default
    try:
        try:
            seconds = float(value)
        except ValueError:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            seconds = (when - now).total_seconds()
        if not math.isfinite(seconds) or seconds < 0:
            return default
        # Check datetime range here, so malformed values cannot escape the outage path.
        now + timedelta(seconds=seconds)
        return max(1.0, seconds)
    except (TypeError, ValueError, OverflowError):
        return default


def symbol(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=-]{0,14}", value):
        raise MarketError("请输入有效股票代码，如 AMZN、0700.HK 或 600519.SS。")
    return value.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[AB]", value) else value


def question_period(question: str) -> str:
    for pattern, period in [
        (r"全部|上市以来|历史全部|\bmax\b|\b(?:all|full|entire)(?: price)? history\b|\bsince (?:IPO|listing)\b", "MAX"),
        (r"(?<!\d)(?:10|十)\s*年|(?<!\d)10y|\b(?:10|ten)[ -]years?\b", "10Y"),
        (r"(?<!\d)(?:5|五)\s*年|(?<!\d)5y|\b(?:5|five)[ -]years?\b", "5Y"),
        (r"(?<!\d)(?:3|三)\s*年|(?<!\d)3y|\b(?:3|three)[ -]years?\b", "3Y"),
        (r"(?<!\d)(?:6|六)\s*个?月|(?<!\d)6m|\b(?:6|six)[ -]months?\b", "6M"),
        (r"(?<!\d)(?:1|一)\s*个?月|(?<!\d)1m|\b(?:1|one)[ -]months?\b", "1M"),
        (r"(?<!\d)(?:1|一|近|过去)\s*年|(?<!\d)1y|\b(?:1|one)[ -]years?\b", "1Y"),
    ]:
        if re.search(pattern, question, re.I):
            return period
    return "5Y"


def unsupported_question_period(question: str) -> bool:
    """Detect an explicit chart window that is outside the supported choices.

    Financial years (``FY2025``/``2025 年报``) and TTM's twelve-month wording
    are deliberately excluded. An omitted chart window continues to default to
    five years for backwards compatibility.
    """
    explicit = re.search(
        r"(?<!\d)\d+\s*(?:years?|y|months?|m)\b|(?<!\d)\d+\s*(?:年(?!报|度|末)|个月)",
        question,
        re.I,
    )
    if not explicit:
        return False
    if re.search(
        r"price|stock|chart|history|trend|return|drawdown|performance|"
        r"走势|股价|行情|回撤|涨跌|基准|比较|对比|"
        r"\b(?:over|past|last)\s+\d+\s+years?\b|(?:近|过去|最近)\d+年",
        question,
        re.I,
    ):
        return question_period(question) == "5Y" and not re.search(
            r"(?<!\d)(?:1|3|5|10)\s*(?:years?|y|months?|m)\b|"
            r"(?<!\d)(?:1|3|5|10)\s*(?:年|个月)",
            question,
            re.I,
        )
    return False


def is_market_question(question: str) -> bool:
    market = re.search(
        r"股价|行情|走势图|趋势图|历史价格|涨跌|回撤|涨了|跌了|price|price chart|stock chart|drawdown|return",
        question,
        re.I,
    )
    other = re.search(
        r"营收|营业|净利润|现金流|估值|市盈率|风险|管理层|年报|为什么|原因|怎么样|值得|revenue|valuation|risk|why",
        question,
        re.I,
    )
    return bool(market and not other)


def is_valuation_question(question: str) -> bool:
    return bool(
        re.search(r"市盈率|\bp/?e\b", question, re.I)
        and not re.search(
            r"贵|便宜|合理|值得|怎么样|为什么|风险|cheap|expensive|worth|why|risk", question, re.I
        )
    )


class PricePoint(StrictModel):
    date: date
    at: int = Field(gt=0)
    close: Decimal = Field(gt=0)
    adjusted: Decimal | None = Field(default=None, gt=0)
    volume: int | None = Field(default=None, ge=0)


class MarketConflict(StrictModel):
    """A same-day disagreement retained alongside the selected market source."""

    ticker: str
    trade_date: date
    currency: str
    left_source: str
    left_value: Decimal = Field(gt=0)
    left_retrieved_at: datetime
    right_source: str
    right_value: Decimal = Field(gt=0)
    right_retrieved_at: datetime
    relative_difference_pct: Decimal
    reason: str


class MarketSourceReference(StrictModel):
    """Inputs needed to replay a secondary source without copying its curve."""

    ticker: str
    period: Literal["1M", "6M", "1Y", "3Y", "5Y", "10Y", "MAX"]
    as_of: date
    retrieved_at: datetime
    source_url: str = Field(max_length=2048)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stale: bool = False

    @model_validator(mode="after")
    def validate_reference(self):
        from .market_backup import backup_url
        from .market_nasdaq import nasdaq_url

        symbol(self.ticker)
        if self.retrieved_at.tzinfo is None:
            raise ValueError("Retrieval time must have a timezone")
        if not self.source_url.startswith(
            (
                "https://query1.finance.yahoo.com/v8/finance/chart/",
                "https://query2.finance.yahoo.com/v8/finance/chart/",
            )
        ) and not backup_url(self.source_url) and not nasdaq_url(self.source_url):
            raise ValueError("Unknown cross-check source")
        return self

    @classmethod
    def from_snapshot(cls, snapshot: "MarketSnapshot") -> "MarketSourceReference":
        return cls(**{key: getattr(snapshot, key) for key in cls.model_fields})


class MarketSnapshot(StrictModel):
    ticker: str
    name: str
    currency: str
    exchange: str
    instrument_type: str = "EQUITY"
    exchange_timezone: str
    period: Literal["1M", "6M", "1Y", "3Y", "5Y", "10Y", "MAX"]
    as_of: date
    retrieved_at: datetime
    source_url: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_name: str = "Yahoo Finance"
    basis: Literal["adjusted_close", "close", "split_adjusted_close"]
    points: list[PricePoint] = Field(min_length=1, max_length=25000)
    quote_value: Decimal = Field(gt=0)
    quote_at: int = Field(gt=0)
    quote_type: Literal["provider_quote", "historical_close"]
    stale: bool = False
    split_dates: list[date] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, max_length=8)
    source_conflicts: list[MarketConflict] = Field(default_factory=list, max_length=8)
    cross_check_source: MarketSourceReference | None = None

    @model_validator(mode="after")
    def validate_series(self):
        symbol(self.ticker)
        zone = ZoneInfo(self.exchange_timezone)
        if self.retrieved_at.tzinfo is None:
            raise ValueError("Retrieval time must have a timezone")
        if self.as_of > self.retrieved_at.astimezone(zone).date():
            raise ValueError("Future knowledge dates are unsupported")
        if not self.source_url.startswith(
            (
                "https://query1.finance.yahoo.com/v8/finance/chart/",
                "https://query2.finance.yahoo.com/v8/finance/chart/",
            )
        ):
            from .market_backup import backup_url
            from .market_nasdaq import nasdaq_url

            if backup_url(self.source_url):
                if self.basis != "split_adjusted_close":
                    raise ValueError("Independent Twelve Data source requires split-adjusted basis")
            elif not nasdaq_url(self.source_url) or self.basis != "close":
                raise ValueError("Unknown market source")
        elif self.basis == "split_adjusted_close":
            raise ValueError("Split-only basis requires the independent daily source")
        previous = None
        for point in self.points:
            if not point.close.is_finite() or (
                point.adjusted is not None and not point.adjusted.is_finite()
            ):
                raise ValueError("Non-finite price")
            if (
                point.date != datetime.fromtimestamp(point.at, zone).date()
                or point.date > self.as_of
            ):
                raise ValueError("Price outside research knowledge date")
            if previous is not None and (point.date <= previous.date or point.at <= previous.at):
                raise ValueError("Price series must be strictly ordered")
            if point.at > self.retrieved_at.timestamp() + 300:
                raise ValueError("Future market timestamp")
            previous = point
        if self.basis == "adjusted_close" and any(p.adjusted is None for p in self.points):
            raise ValueError("Cannot mix adjusted and unadjusted prices")
        if (
            not self.quote_value.is_finite()
            or datetime.fromtimestamp(self.quote_at, zone).date() > self.as_of
            or self.quote_at > self.retrieved_at.timestamp() + 300
        ):
            raise ValueError("Invalid quote timestamp/value")
        if self.quote_type == "historical_close" and (
            self.quote_at != self.points[-1].at or self.quote_value != self.points[-1].close
        ):
            raise ValueError("Historical quote must be the last available close")
        if any(day > self.as_of for day in self.split_dates):
            raise ValueError("Split event beyond the knowledge date")
        return self


def _number(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except Exception:
        return None


def _months_before(day: date, months: int) -> date:
    count = day.year * 12 + day.month - 1 - months
    year, month = divmod(count, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def parse_chart(
    raw: bytes,
    ticker: str,
    period: str,
    as_of: date,
    retrieved_at: datetime,
    url: str,
    *,
    stale=False,
) -> MarketSnapshot:
    if period not in PERIODS:
        raise MarketError("可用周期：1M / 6M / 1Y / 3Y / 5Y / 10Y / MAX。")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or not isinstance(body.get("chart"), dict):
            raise ValueError("Invalid chart envelope")
        provider_error = body["chart"].get("error")
        if provider_error is not None and not isinstance(provider_error, dict):
            raise ValueError("Invalid chart error envelope")
        if (provider_error or {}).get("code") == "Not Found":
            raise MarketError("该行情源暂无此证券价格，请核对上市市场或选择其他证券。")
        result = body["chart"]["result"][0]
        meta = result["meta"]
        if symbol(meta["symbol"]) != symbol(ticker):
            raise ValueError("Provider returned a different symbol")
        zone = ZoneInfo(meta["exchangeTimezoneName"])
        if as_of > max(
            retrieved_at.astimezone(zone).date(),
            retrieved_at.astimezone(UTC).date(),
            retrieved_at.astimezone().date(),
        ):
            raise MarketError("知悉日期不能晚于今天。")
        # UTC users may select today's date before the exchange local day starts.
        effective_date = min(as_of, retrieved_at.astimezone(zone).date())
        cutoff = _months_before(effective_date, PERIODS[period]) if PERIODS[period] else date.min
        timestamps = result["timestamp"]
        quotes = result["indicators"]["quote"][0]
        closes = quotes["close"]
        adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
        volumes = quotes.get("volume", [])
        points, seen = [], set()
        for index, stamp in enumerate(timestamps):
            stamp = int(stamp)
            day = datetime.fromtimestamp(stamp, zone).date()
            close = _number(closes[index])
            if (
                close is None
                or day < cutoff
                or day > effective_date
                or stamp > retrieved_at.timestamp() + 300
            ):
                continue
            if day in seen:
                raise ValueError("Duplicate trading date")
            seen.add(day)
            volume = volumes[index] if index < len(volumes) else None
            points.append(
                PricePoint(
                    date=day,
                    at=stamp,
                    close=close,
                    adjusted=_number(adjusted[index]) if index < len(adjusted) else None,
                    volume=volume,
                )
            )
        points.sort(key=lambda p: p.at)
        if not points:
            raise MarketError("该代码在所选日期和周期内没有可用交易日。可改为今天或更长周期。")
        basis = "adjusted_close" if all(p.adjusted is not None for p in points) else "close"
        quote_at = int(meta.get("regularMarketTime") or 0)
        quote_value = _number(meta.get("regularMarketPrice"))
        quote_type = "provider_quote"
        if (
            not quote_value
            or not quote_at
            or quote_at > retrieved_at.timestamp() + 300
            or datetime.fromtimestamp(quote_at, zone).date() > effective_date
        ):
            quote_at, quote_value, quote_type = points[-1].at, points[-1].close, "historical_close"
        warnings = ["行情可能延迟；图表按交易所日期显示。"]
        if basis == "adjusted_close":
            warnings.append(
                "曲线使用供应商当前复权收盘价，考虑拆股和分红；历史知悉日期不等于当时版本的复权序列。"
            )
        else:
            warnings.append(
                "复权数据不完整，整条曲线使用供应商 Close 收盘价；该序列可能已做拆股调整，未完成分红复权；不应视为历史当时的实际成交价。"
            )
        if stale:
            warnings.append("网络请求未完成，正在使用保存的历史响应，请留意抓取时间。")
        return MarketSnapshot(
            ticker=symbol(ticker),
            name=meta.get("longName") or meta.get("shortName") or ticker,
            currency=meta["currency"],
            exchange=meta.get("exchangeName", ""),
            instrument_type=meta.get("instrumentType", "EQUITY"),
            exchange_timezone=meta["exchangeTimezoneName"],
            period=period,
            as_of=effective_date,
            retrieved_at=retrieved_at,
            source_url=url,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            source_name="Yahoo Finance",
            basis=basis,
            points=points,
            quote_value=quote_value,
            quote_at=quote_at,
            quote_type=quote_type,
            stale=stale,
            split_dates=sorted(
                {
                    datetime.fromtimestamp(int(e["date"]), zone).date()
                    for e in result.get("events", {}).get("splits", {}).values()
                    if datetime.fromtimestamp(int(e["date"]), zone).date() <= effective_date
                }
            ),
            warnings=warnings,
        )
    except MarketError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise _MarketResponseError("行情供应商响应不完整，暂时无法读取价格。") from exc


def parse_market(raw, ticker, period, as_of, retrieved_at, url, *, stale=False):
    from .market_backup import backup_url, parse_backup
    from .market_nasdaq import nasdaq_url, parse_nasdaq

    parser = parse_backup if backup_url(url) else parse_nasdaq if nasdaq_url(url) else parse_chart
    return parser(raw, ticker, period, as_of, retrieved_at, url, stale=stale)


@dataclass(frozen=True)
class SourceFailure:
    """Typed source failure used by the router without exposing provider details."""

    source: str
    ticker: str
    kind: Literal["connection", "rate_limited", "no_coverage", "upstream", "invalid"]
    message: str
    error: Exception


class MarketQuote(StrictModel):
    source_name: str
    ticker: str
    currency: str
    value: Decimal = Field(gt=0)
    at: int = Field(gt=0)
    trade_date: date
    retrieved_at: datetime
    basis: Literal["adjusted_close", "close", "split_adjusted_close"]

    @classmethod
    def from_snapshot(cls, snapshot: MarketSnapshot) -> "MarketQuote":
        return cls(
            source_name=snapshot.source_name,
            ticker=snapshot.ticker,
            currency=snapshot.currency,
            value=snapshot.quote_value,
            at=snapshot.quote_at,
            trade_date=datetime.fromtimestamp(
                snapshot.quote_at, ZoneInfo(snapshot.exchange_timezone)
            ).date(),
            retrieved_at=snapshot.retrieved_at,
            basis=snapshot.basis,
        )


class MarketSource(Protocol):
    name: str

    def fetch_daily(
        self, ticker: str, period: str, as_of: date, *, refresh: bool = False
    ) -> MarketSnapshot | SourceFailure: ...

    def fetch_quote(
        self, ticker: str, as_of: date, *, refresh: bool = False
    ) -> MarketQuote | SourceFailure: ...


def _source_failure(source: str, ticker: str, error: Exception) -> SourceFailure:
    if isinstance(error, MarketUnavailable):
        kind = error.status.get("cause", "upstream")
    elif isinstance(error, MarketError) and any(
        marker in str(error) for marker in ("暂无此证券", "未找到", "仅支持")
    ):
        kind = "no_coverage"
    elif isinstance(error, (httpx.ConnectError, httpx.ReadTimeout, httpx.TimeoutException)):
        kind = "connection"
    elif isinstance(error, httpx.HTTPError):
        kind = "upstream"
    elif isinstance(error, _MarketResponseError):
        kind = "upstream"
    else:
        kind = "invalid"
    return SourceFailure(source, ticker, kind, str(error), error)


class YahooSource:
    """Protocol adapter around the validated Yahoo response/cache implementation."""

    name = "Yahoo Finance"

    def __init__(self, owner: "MarketData"):
        self.owner = owner

    def fetch_daily(self, ticker, period, as_of, *, refresh=False):
        try:
            return self.owner._yahoo_history(ticker, period, as_of, refresh=refresh)
        except (MarketError, httpx.HTTPError) as exc:
            return _source_failure(self.name, ticker, exc)

    def fetch_quote(self, ticker, as_of, *, refresh=False):
        result = self.fetch_daily(ticker, "1M", as_of, refresh=refresh)
        if isinstance(result, SourceFailure):
            return result
        return MarketQuote.from_snapshot(result)


class TwelveDataSource:
    """Independent split-adjusted daily source, used only when configured."""

    name = "Twelve Data"

    def __init__(self, backup):
        self.backup = backup

    def fetch_daily(self, ticker, period, as_of, *, refresh=False):
        if self.backup is None:
            return SourceFailure(
                self.name,
                ticker,
                "no_coverage",
                "独立行情源未配置",
                MarketError("独立行情源未配置"),
            )
        try:
            return self.backup.history(ticker, period, as_of)
        except (MarketError, httpx.HTTPError) as exc:
            return _source_failure(self.name, ticker, exc)

    def fetch_quote(self, ticker, as_of, *, refresh=False):
        result = self.fetch_daily(ticker, "1M", as_of, refresh=refresh)
        if isinstance(result, SourceFailure):
            return result
        return MarketQuote.from_snapshot(result)


class MarketRouter:
    """Route primary/backup sources and make disagreements explicit."""

    def __init__(
        self,
        primary: MarketSource,
        backup: MarketSource | None = None,
        fallback: MarketSource | None = None,
    ):
        self.primary = primary
        self.backup = backup
        self.fallback = fallback

    @staticmethod
    def _with_conflict(primary: MarketSnapshot, backup: MarketSnapshot) -> MarketSnapshot:
        primary = primary.model_copy(
            update={"cross_check_source": MarketSourceReference.from_snapshot(backup)}
        )
        if primary.ticker != backup.ticker or primary.currency != backup.currency:
            return primary.model_copy(
                update={
                    "warnings": [
                        *primary.warnings,
                        f"{backup.source_name} 的证券代码或币种与主来源不一致；未比较数值或合并曲线。",
                    ][:8]
                }
            )
        left, right = primary.points[-1], backup.points[-1]
        if left.date != right.date:
            return primary.model_copy(
                update={
                    "warnings": [
                        *primary.warnings,
                        f"{backup.source_name} 最近交易日期为 {right.date.isoformat()}，与主来源 {left.date.isoformat()} 不同；未合并两条曲线。",
                    ][:8]
                }
            )
        # Quotes may be intraday or newer than the daily history. Comparing
        # them to a completed close can both fabricate and hide conflicts.
        # Use the same-date Close observations, never the dividend-adjusted
        # values or the live quote. Keep the selected quote untouched.
        difference = abs(left.close / right.close - 1) * 100
        if difference <= Decimal("0.50"):
            return primary
        conflict = MarketConflict(
            ticker=primary.ticker,
            trade_date=left.date,
            currency=primary.currency,
            left_source=primary.source_name,
            left_value=left.close,
            left_retrieved_at=primary.retrieved_at,
            right_source=backup.source_name,
            right_value=right.close,
            right_retrieved_at=backup.retrieved_at,
            relative_difference_pct=difference,
            reason="同一交易日两家来源收盘值差异超过 0.50%；未取平均值。",
        )
        return primary.model_copy(
            update={
                "source_conflicts": [*primary.source_conflicts, conflict][:8],
                "warnings": [
                    *primary.warnings,
                    f"{primary.source_name} 与 {backup.source_name} 的 {left.date.isoformat()} 收盘值存在 {difference:.2f}% 差异；请在证据中分别核对。",
                ][:8],
            }
        )

    def fetch_daily(self, ticker, period, as_of, *, refresh=False):
        primary = self.primary.fetch_daily(ticker, period, as_of, refresh=refresh)
        if isinstance(primary, SourceFailure):
            # A symbol miss or an explicit provider cooldown is already a
            # deterministic outcome; do not turn it into a second-provider
            # request. Network/upstream failures may use the independent path.
            if primary.kind in ("no_coverage", "invalid"):
                return primary
            sources = (self.backup,) if primary.kind == "rate_limited" else (self.backup, self.fallback)
            for source in sources:
                if source is None:
                    continue
                fallback = source.fetch_daily(ticker, period, as_of, refresh=refresh)
                if not isinstance(fallback, SourceFailure):
                    return fallback
            return primary
        if self.backup is None:
            return primary
        backup = self.backup.fetch_daily(ticker, period, as_of, refresh=refresh)
        if isinstance(backup, SourceFailure):
            return primary
        return self._with_conflict(primary, backup)

    def fetch_quote(self, ticker, as_of, *, refresh=False):
        result = self.fetch_daily(ticker, "1M", as_of, refresh=refresh)
        if isinstance(result, SourceFailure):
            return result
        return MarketQuote.from_snapshot(result)


def statistics(snapshot: MarketSnapshot) -> dict:
    values = [
        p.adjusted if snapshot.basis == "adjusted_close" else p.close for p in snapshot.points
    ]
    first, last = values[0], values[-1]
    ordered = sorted(values)

    def nearest_rank(fraction: Decimal) -> Decimal:
        # A nearest-rank percentile keeps this presentation deterministic and
        # never invents a price between two observed trading days.
        index = min(
            len(ordered) - 1,
            max(0, int((len(ordered) * fraction).to_integral_value(rounding="ROUND_CEILING")) - 1),
        )
        return ordered[index]

    peak, drawdown = first, Decimal(0)
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, (value / peak - 1) * 100)
    return {
        "start": snapshot.points[0].date.isoformat(),
        "end": snapshot.points[-1].date.isoformat(),
        "trading_days": len(values),
        "first": str(first),
        "last": str(last),
        "low": str(ordered[0]),
        "p25": str(nearest_rank(Decimal("0.25"))),
        "median": str(nearest_rank(Decimal("0.50"))),
        "p75": str(nearest_rank(Decimal("0.75"))),
        "high": str(ordered[-1]),
        "change_pct": str((last / first - 1) * 100),
        "max_drawdown_pct": str(drawdown),
        "quote_value": str(snapshot.quote_value),
        "quote_at": datetime.fromtimestamp(
            snapshot.quote_at, ZoneInfo(snapshot.exchange_timezone)
        ).isoformat(),
    }


def render_market(snapshot: MarketSnapshot) -> str:
    stats = statistics(snapshot)
    basis = (
        "复权收盘价"
        if snapshot.basis == "adjusted_close"
        else "拆股调整收盘价（未含股息调整）"
        if snapshot.basis == "split_adjusted_close"
        else "供应商 Close 收盘价"
    )
    label = "行情报价" if snapshot.quote_type == "provider_quote" else "历史收盘价"
    prefix = (
        ("使用保存的行情响应。" if snapshot.stale else "")
        + f"{snapshot.ticker} {label}：{snapshot.quote_value:,.2f} {snapshot.currency}，"
        + (
            f"交易日期 {stats['quote_at'][:10]}（原文未提供精确时刻）。"
            if snapshot.basis == "split_adjusted_close"
            else f"交易所时间 {stats['quote_at']}。"
        )
    )
    source_note = ""
    if snapshot.source_conflicts:
        source_note = " 来源差异：两家供应商同一交易日的数值已分别保留，未取平均值。"
    elif snapshot.warnings:
        source_note = " " + " ".join(snapshot.warnings[:2])
    if len(snapshot.points) == 1:
        return prefix + "所选区间仅有一个交易日，不能计算区间涨跌和回撤。" + source_note
    return (
        prefix
        + f"{stats['start']} 至 {stats['end']}（{stats['trading_days']} 个交易日），{basis}区间变化 {Decimal(stats['change_pct']):+.2f}%，最大回撤 {Decimal(stats['max_drawdown_pct']):.2f}%。区间变化未年化，复权数据采用供应商当前版本。"
        + source_note
    )


class MarketEvidence(StrictModel):
    kind: Literal["market"] = "market"
    id: str
    ticker: str
    snapshot: MarketSnapshot

    @model_validator(mode="after")
    def identity(self):
        expected = market_evidence(self.snapshot)
        if self.ticker != self.snapshot.ticker or self.id != expected["id"]:
            raise ValueError("Market evidence identity mismatch")
        return self


def market_evidence(snapshot: MarketSnapshot) -> dict:
    scope = f"{snapshot.source_sha256}:{snapshot.ticker}:{snapshot.period}:{snapshot.as_of}"
    return {
        "kind": "market",
        "id": "market:" + hashlib.sha256(scope.encode()).hexdigest()[:24],
        "ticker": snapshot.ticker,
        "snapshot": snapshot.model_dump(mode="json"),
    }


class MarketData:
    def __init__(
        self,
        home: Path,
        *,
        client=None,
        now=None,
        ttl=300,
        directory_path=None,
        failure_ttl=30,
        backup_key="",
    ):
        self.cache = home / "market-cache"
        self.directory = StockDirectory(directory_path)
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20, connect=8),
            headers={
                "User-Agent": "FilingCopilot/0.6 public-market-research",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )
        self.now = now or (lambda: datetime.now(UTC))
        self.ttl = ttl
        self.failure_ttl = max(1, min(60, failure_ttl))
        self.lock = threading.Lock()
        self.symbol_locks: dict[str, threading.Lock] = {}
        self.failures: dict[str, _MarketFailure] = {}
        self.provider_failure: _MarketFailure | None = None
        from .market_backup import IndependentMarket

        self.backup = IndependentMarket(self, backup_key) if backup_key else None
        self.yahoo_source = YahooSource(self)
        self.twelve_data_source = TwelveDataSource(self.backup) if self.backup else None
        from .market_nasdaq import NasdaqSource

        self.nasdaq_source = NasdaqSource(self)
        self.router = MarketRouter(self.yahoo_source, self.twelve_data_source, self.nasdaq_source)
        from .valuation import ValuationData

        self.valuations = ValuationData(
            home,
            client=self.client,
            now=self.now,
            ttl=ttl,
            availability=self.availability,
            rate_limit=lambda ticker, headers: self._record_failure(
                ticker, "rate_limited", headers=headers
            ),
        )

    def _failure(self, ticker: str, now: datetime) -> _MarketFailure | None:
        with self.lock:
            provider, local = self.provider_failure, self.failures.get(ticker)
            if provider and now < provider.retry_at:
                return provider
            return local or provider

    def _record_failure(self, ticker, cause, *, headers=None):
        now = self.now()
        seconds = (
            _retry_seconds(headers or {}, now, self.failure_ttl)
            if cause == "rate_limited"
            else self.failure_ttl
        )
        failure = _MarketFailure(
            cause,
            "provider" if cause == "rate_limited" else "symbol",
            now + timedelta(seconds=seconds),
        )
        with self.lock:
            if failure.scope == "provider":
                previous = self.provider_failure
                if not previous or failure.retry_at > previous.retry_at:
                    self.provider_failure = failure
                else:
                    failure = previous
            else:
                self.failures[ticker] = failure
                if len(self.failures) > 512:
                    oldest = min(self.failures, key=lambda key: self.failures[key].retry_at)
                    self.failures.pop(oldest)
        return failure

    def _success(self, ticker, now):
        with self.lock:
            self.failures.pop(ticker, None)
            # An already in-flight success does not waive another request's Retry-After.
            if self.provider_failure and now >= self.provider_failure.retry_at:
                self.provider_failure = None

    def availability(self, ticker: str) -> dict | None:
        now = self.now()
        failure = self._failure(symbol(ticker), now)
        if not failure or now >= failure.retry_at:
            return None
        error = MarketUnavailable(ticker, failure, now)
        return {**error.status, "message": str(error)}

    def source_status(self) -> dict:
        """Describe configured market sources without exposing credentials.

        This is a capability contract for the UI and API: a primary source may
        be available while an independent fallback is deliberately absent.
        Keeping that distinction visible prevents a chart outage from looking
        like a missing filing or a user input error.
        """
        return {
            "primary": {
                "name": self.yahoo_source.name,
                "configured": True,
                "role": "primary_daily_history",
                "basis": "provider_adjusted_close_when_available",
            },
            "fallbacks": [
                {
                    "name": "Twelve Data",
                    "configured": self.backup is not None,
                    "independent": True,
                    "role": "independent_daily_history_and_conflict_check",
                    "coverage": "US equities and ETFs when configured",
                },
                {
                    "name": "Nasdaq",
                    "configured": True,
                    "independent": True,
                    "role": "public_historical_daily_fallback",
                    "coverage": "US-listed securities with public Nasdaq history",
                },
            ],
            "independent_fallback_configured": True,
            "routing": "Yahoo, then Twelve Data when configured, then Nasdaq public history; conflicts_are_retained",
        }

    @staticmethod
    def _saved(cached, ticker, period, as_of):
        # Preserve source bytes, acquisition time and the existing auditable snapshot schema.
        return parse_market(
            cached[0],
            ticker,
            period,
            min(as_of, cached[2].date()),
            cached[2],
            cached[1],
            stale=True,
        )

    def _read(self, ticker):
        try:
            path = self.cache / self._index_name(ticker)
            if not path.is_file():
                path = self.cache / (ticker.replace("^", "index-") + ".json")
            index = json.loads(path.read_text("utf-8"))
            sha = index["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                return None
            raw = (self.cache / "raw" / (sha + ".json")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                return None
            retrieved = datetime.fromisoformat(index["retrieved_at"])
            if retrieved.tzinfo is None:
                return None
            return raw, index["url"], retrieved
        except (OSError, ValueError, KeyError):
            return None

    @staticmethod
    def _index_name(ticker):
        return "symbol-" + hashlib.sha256(ticker.encode()).hexdigest()[:24] + ".json"

    def _save(self, ticker, raw, url, now):
        folder = self.cache / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256(raw).hexdigest()
        raw_path = folder / (sha + ".json")
        if not raw_path.exists():
            temp = folder / (uuid.uuid4().hex + ".tmp")
            temp.write_bytes(raw)
            temp.replace(raw_path)
        path = self.cache / self._index_name(ticker)
        temp = self.cache / (uuid.uuid4().hex + ".tmp")
        temp.write_text(
            json.dumps({"sha256": sha, "url": url, "retrieved_at": now.isoformat()}), "utf-8"
        )
        temp.replace(path)

    def history(self, ticker: str, period="5Y", as_of: date | None = None, *, refresh=False):
        ticker, now = symbol(ticker), self.now()
        as_of = as_of or now.date()
        if period not in PERIODS or as_of > max(now.date(), now.astimezone().date()):
            raise MarketError("周期无效，或知悉日期晚于今天。")
        result = self.router.fetch_daily(ticker, period, as_of, refresh=refresh)
        if isinstance(result, SourceFailure):
            raise result.error
        return result

    def _yahoo_history(
        self, ticker: str, period="5Y", as_of: date | None = None, *, refresh=False
    ) -> MarketSnapshot:
        ticker, now = symbol(ticker), self.now()
        as_of = as_of or now.date()
        if period not in PERIODS or as_of > max(now.date(), now.astimezone().date()):
            raise MarketError("周期无效，或知悉日期晚于今天。")
        with self.lock:
            ticker_lock = self.symbol_locks.setdefault(ticker, threading.Lock())
        with cancellable_lock(ticker_lock):
            now = self.now()
            cached = self._read(ticker)
            failure = self._failure(ticker, now)
            if (
                failure
                and now < failure.retry_at
                and (failure.cause == "rate_limited" or not refresh)
            ):
                if cached:
                    return self._saved(cached, ticker, period, as_of)
                raise MarketUnavailable(ticker, failure, now)
            if (
                cached
                and not refresh
                and not failure
                and 0 <= (now - cached[2]).total_seconds() < self.ttl
                and as_of <= max(cached[2].date(), cached[2].astimezone().date())
            ):
                return parse_chart(*cached[:1], ticker, period, as_of, cached[2], cached[1])
            failure = None
            errors = []
            for host in ("query1", "query2"):
                checkpoint()
                url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}?period1=0&period2={int((now + timedelta(days=1)).timestamp())}&interval=1d&events=div%2Csplits"
                try:
                    progress("market_request", "正在读取行情供应商历史价格；当前请求有超时上限。")
                    response = self.client.get(
                        url, timeout=httpx.Timeout(bounded_timeout(20), connect=bounded_timeout(8))
                    )
                    # Yahoo uses HTTP404 for an explicit chart "Not Found" envelope.
                    # Parse that bounded response before classifying a transport failure.
                    if response.status_code == 404 and len(response.content) <= 10_000_000:
                        parse_chart(response.content, ticker, period, as_of, self.now(), url)
                    response.raise_for_status()
                    if len(response.content) > 10_000_000:
                        raise _MarketResponseError("行情响应过大。")
                    received_at = self.now()
                    parsed = parse_chart(response.content, ticker, period, as_of, received_at, url)
                    self._save(ticker, response.content, url, received_at)
                    self._success(ticker, received_at)
                    return parsed
                except (httpx.HTTPError, MarketError) as exc:
                    errors.append(exc)
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        failure = self._record_failure(
                            ticker, "rate_limited", headers=exc.response.headers
                        )
                        break  # The second Yahoo hostname is not an independent quota.
            if not failure or failure.retry_at <= self.now():
                # Valid no-data/period results remain input errors and do not pause other queries.
                if errors and all(
                    isinstance(error, (httpx.HTTPError, _MarketResponseError)) for error in errors
                ):
                    cause = (
                        "connection"
                        if all(isinstance(error, httpx.RequestError) for error in errors)
                        else "upstream"
                    )
                    failure = self._record_failure(ticker, cause)
            if cached:
                # The source timestamp is retained. Do not falsely label saved quotes as new.
                return self._saved(cached, ticker, period, as_of)
            # Keep valid no-data/period errors distinct from an unavailable connection.
            no_data = next(
                (
                    str(e)
                    for e in errors
                    if isinstance(e, MarketError) and not isinstance(e, _MarketResponseError)
                ),
                None,
            )
            if no_data:
                raise MarketError(f"{ticker}：{no_data}")
            if failure:
                raise MarketUnavailable(ticker, failure, self.now())
            raise MarketError(
                f"{ticker} 行情源连接未完成，尚未取得价格数据。"
                "请点击“重试行情”；这不需要先下载财报。也可重新选择交易所代码。"
            )

    def _nasdaq_history(self, ticker, period="5Y", as_of=None, *, refresh=False):
        """Fetch public Nasdaq daily history after Yahoo and optional Twelve Data fail."""
        from .market_nasdaq import SOURCE, parse_nasdaq

        ticker, now = symbol(ticker), self.now()
        as_of = as_of or now.date()
        if period not in PERIODS or as_of > max(now.date(), now.astimezone().date()):
            raise MarketError("周期无效，或知悉日期晚于今天。")
        with self.lock:
            ticker_lock = self.symbol_locks.setdefault("nasdaq-" + ticker, threading.Lock())
        with cancellable_lock(ticker_lock):
            now = self.now()
            cached = self._read(ticker)
            if (
                cached
                and not refresh
                and cached[1].startswith(SOURCE + "/")
                and 0 <= (now - cached[2]).total_seconds() < self.ttl
            ):
                return self._saved(cached, ticker, period, as_of)
            start = _months_before(as_of, PERIODS[period]) if PERIODS[period] else date(1900, 1, 1)
            url = (
                SOURCE
                + "/"
                + quote(ticker, safe="")
                + "/historical?"
                + urlencode(
                    {
                        "assetclass": "stocks",
                        "fromdate": start.isoformat(),
                        "todate": as_of.isoformat(),
                        "limit": 5000,
                        "offset": 0,
                    }
                )
            )
            checkpoint()
            progress("nasdaq_market", "Yahoo 行情未完成，正在接管读取 Nasdaq 历史日线。")
            response = self.client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 FilingCopilot/market-fallback",
                    "Accept": "application/json,text/plain,*/*",
                    "Origin": "https://www.nasdaq.com",
                    "Referer": "https://www.nasdaq.com/",
                },
                timeout=httpx.Timeout(bounded_timeout(15), connect=bounded_timeout(5)),
            )
            response.raise_for_status()
            if len(response.content) > 10_000_000:
                raise MarketError("Nasdaq fallback response is too large.")
            parsed = parse_nasdaq(response.content, ticker, period, as_of, self.now(), url)
            self._save(ticker, response.content, url, parsed.retrieved_at)
            return parsed

    def search(self, query: str, domain) -> dict:
        query = query.strip()
        if not query or len(query) > 120:
            raise MarketError("请输入公司名称或股票代码。")
        identified = self.directory.lookup(query)
        if identified:
            return {
                "query": query,
                "candidates": identified,
                "match_count": len(identified),
                "identity_source": "listing_directory",
                "message": "请选择上市市场和币种；同一公司的不同上市证券分别报价。选中后获取行情，无需下载财报。",
            }
        local = domain.company_scope(query)
        candidates = [
            {
                "ticker": t,
                "name": domain.companies[t].name,
                "exchange": "SEC",
                "type": "EQUITY",
                "filings_supported": True,
            }
            for t in sorted(local)
        ]
        # Explicit symbols can use the chart endpoint even when the search endpoint throttles.
        if not candidates and re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=-]{0,14}", query):
            try:
                snapshot = self.history(query, "1M")
                candidates = [
                    {
                        "ticker": snapshot.ticker,
                        "name": snapshot.name,
                        "exchange": snapshot.exchange,
                        "type": snapshot.instrument_type,
                        "filings_supported": snapshot.instrument_type == "EQUITY"
                        and snapshot.exchange in {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX"},
                    }
                ]
            except MarketError:
                pass
        if not candidates:
            try:
                response = self.client.get(
                    "https://query1.finance.yahoo.com/v1/finance/search",
                    params={"q": query, "quotesCount": 8, "newsCount": 0},
                    timeout=httpx.Timeout(bounded_timeout(20), connect=bounded_timeout(8)),
                )
                response.raise_for_status()
                for row in response.json().get("quotes", []):
                    if row.get("quoteType") not in ("EQUITY", "ETF", "INDEX"):
                        continue
                    ticker = symbol(row["symbol"])
                    candidates.append(
                        {
                            "ticker": ticker,
                            "name": row.get("longname") or row.get("shortname") or ticker,
                            "exchange": row.get("exchange", ""),
                            "type": row["quoteType"],
                            "filings_supported": ticker in domain.companies
                            or (
                                row["quoteType"] == "EQUITY"
                                and row.get("exchange")
                                in {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX"}
                            ),
                        }
                    )
            except (httpx.HTTPError, ValueError, KeyError):
                raise MarketError(
                    "股票搜索暂未返回，请输入明确代码，如 AMZN / 0700.HK / 600519.SS。"
                ) from None
        return {
            "query": query,
            "candidates": candidates,
            "match_count": len(candidates),
            "message": "请选择股票以查看行情；美国 SEC 公司可继续准备财报。"
            if candidates
            else "未找到匹配股票。可尝试英文名称或交易所代码。",
        }

    def filing_scope(self, ticker: str) -> bool | None:
        """Describe known cached instrument coverage without fetching or calling a model."""
        listing = self.directory.listing(symbol(ticker))
        if listing:
            return listing.filings_supported
        cached = self._read(symbol(ticker))
        if not cached:
            return None
        try:
            meta = json.loads(cached[0])["chart"]["result"][0]["meta"]
            if symbol(meta["symbol"]) != symbol(ticker):
                return None
            return meta.get("instrumentType", "EQUITY") == "EQUITY" and meta.get(
                "exchangeName"
            ) in {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX"}
        except (ValueError, KeyError, TypeError, IndexError):
            return None
