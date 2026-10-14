"""Supplier TTM EPS snapshots, kept separate from SEC financial facts.

The supplier's period label is not asserted to be a fiscal period end. A saved
response can be re-extracted offline; this does not certify its EPS methodology.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import Field, model_validator

from .corpus import stable_id
from .execution import bounded_timeout, cancellable_lock, checkpoint, progress
from .market import MarketEvidence, symbol
from .models import Calculation, StrictModel

NOTE = (
    "Yahoo Finance 供应商 TTM 稀释 EPS 口径；期末标签可能按日历季度标准化，"
    "不代表公司的实际财季末。未逐季重建 SEC 加权稀释股数或独立核对供应商拆股调整；"
    "不是预测 EPS，单一市盈率不能证明股票便宜或昂贵。"
)


def is_provider_valuation_question(question):
    from .ttm import is_ttm_question

    return bool(
        is_ttm_question(question) and re.search(r"每股|EPS|市盈率|\bp/?e\b", question, re.I)
    )


class ValuationSnapshot(StrictModel):
    ticker: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    value: Decimal
    period_end_label: date
    period_type: Literal["TTM"] = "TTM"
    retrieved_at: datetime
    source_url: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stale: bool = False

    @model_validator(mode="after")
    def validate_snapshot(self):
        if symbol(self.ticker) != self.ticker:
            raise ValueError("Non-canonical valuation symbol")
        if not self.value.is_finite() or self.retrieved_at.tzinfo is None:
            raise ValueError("Invalid EPS value or retrieval time")
        if self.period_end_label > self.retrieved_at.date():
            raise ValueError("Future EPS period label")
        url = urlsplit(self.source_url)
        if (
            url.scheme != "https"
            or url.netloc not in ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
            or url.path
            != "/ws/fundamentals-timeseries/v1/finance/timeseries/" + quote(self.ticker, safe="")
        ):
            raise ValueError("Unknown EPS source")
        return self


class ValuationEvidence(StrictModel):
    id: str
    kind: Literal["valuation"] = "valuation"
    ticker: str
    snapshot: ValuationSnapshot

    @model_validator(mode="after")
    def validate_identity(self):
        if self.ticker != self.snapshot.ticker or self.id != evidence_id(self.snapshot):
            raise ValueError("EPS evidence identity mismatch")
        return self


def evidence_id(snapshot):
    return stable_id("valuation", snapshot.model_dump_json())


def valuation_evidence(snapshot):
    return ValuationEvidence(
        id=evidence_id(snapshot), ticker=snapshot.ticker, snapshot=snapshot
    ).model_dump(mode="json")


def parse_eps(raw: bytes, ticker: str, retrieved_at: datetime, url: str, *, stale=False):
    try:
        if len(raw) > 1_000_000:
            raise ValueError("Oversized response")
        result = json.loads(raw, parse_float=Decimal)["timeseries"]
        if result.get("error") or len(result["result"]) != 1:
            raise ValueError("Missing supplier EPS series")
        series = result["result"][0]
        if series["meta"]["symbol"] != [ticker] or series["meta"]["type"] != ["trailingDilutedEPS"]:
            raise ValueError("Different supplier symbol or metric")
        rows = [
            r
            for r in series["trailingDilutedEPS"]
            if date.fromisoformat(r["asOfDate"]) <= retrieved_at.date()
        ]
        if not rows:
            raise ValueError("No disclosed EPS observation")
        latest = max(r["asOfDate"] for r in rows)
        candidates = [r for r in rows if r["asOfDate"] == latest]
        signatures = {
            (str(r["reportedValue"]["raw"]), r["currencyCode"], r["periodType"]) for r in candidates
        }
        if len(signatures) != 1 or candidates[0]["periodType"] != "TTM":
            raise ValueError("Conflicting EPS observations or non-TTM period")
        row = candidates[0]
        if isinstance(row["reportedValue"]["raw"], bool):
            raise ValueError("Boolean EPS")
        return ValuationSnapshot(
            ticker=ticker,
            currency=row["currencyCode"],
            value=Decimal(str(row["reportedValue"]["raw"])),
            period_end_label=date.fromisoformat(latest),
            retrieved_at=retrieved_at,
            source_url=url,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            stale=stale,
        )
    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
        raise ValueError(
            "供应商未返回可核对的 TTM 稀释 EPS；可先查询年度 EPS 市盈率或财报中的盈利和现金流。"
        ) from exc


class ValuationData:
    def __init__(self, home, *, client, now, ttl=300, availability=None, rate_limit=None):
        self.cache = home / "market-cache"
        self.client, self.now, self.ttl = client, now, ttl
        self.lock = threading.Lock()
        self.symbol_locks = {}
        self.availability, self.rate_limit = availability, rate_limit

    def _index(self, ticker):
        return self.cache / ("eps-" + hashlib.sha256(ticker.encode()).hexdigest()[:24] + ".json")

    def _read(self, ticker):
        try:
            entry = json.loads(self._index(ticker).read_text("utf-8"))
            sha = entry["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                return None
            raw = (self.cache / "raw" / (sha + ".json")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                return None
            at = datetime.fromisoformat(entry["retrieved_at"])
            return parse_eps(raw, ticker, at, entry["url"]), raw
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save(self, ticker, snapshot, raw):
        folder = self.cache / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (snapshot.source_sha256 + ".json")
        temp = folder / (uuid.uuid4().hex + ".tmp")
        temp.write_bytes(raw)
        temp.replace(path)
        temp = self.cache / (uuid.uuid4().hex + ".tmp")
        temp.write_text(
            json.dumps(
                {
                    "sha256": snapshot.source_sha256,
                    "url": snapshot.source_url,
                    "retrieved_at": snapshot.retrieved_at.isoformat(),
                }
            ),
            "utf-8",
        )
        temp.replace(self._index(ticker))

    def eps(self, ticker: str, as_of: date):
        ticker, now = symbol(ticker), self.now().astimezone()
        with self.lock:
            lock = self.symbol_locks.setdefault(ticker, threading.Lock())
        with cancellable_lock(lock):
            now = self.now().astimezone()
            cached = self._read(ticker)
            # A request started just before midnight may finish just after it.
            # Preserve a cache from the requested day, but never make a network
            # request for a historical as-of date or silently relabel the cache.
            if as_of != now.date():
                if cached and cached[0].retrieved_at.date() == as_of and cached[0].retrieved_at <= now:
                    return cached[0].model_copy(update={"stale": True})
                raise ValueError(
                    "供应商 TTM EPS 只支持当前知悉日期，历史发布版本未接入；历史研究可选择 SEC 年度或 TTM 流量数据。"
                )
            if (
                cached
                and cached[0].retrieved_at.date() == as_of
                and 0 <= (now - cached[0].retrieved_at).total_seconds() < self.ttl
            ):
                return cached[0]
            pause = self.availability(ticker) if self.availability else None
            if pause and pause["cause"] == "rate_limited":
                if cached and cached[0].retrieved_at <= now:
                    return cached[0].model_copy(update={"stale": True})
                raise ValueError(pause["message"])
            for host in ("query1", "query2"):
                checkpoint()
                url = f"https://{host}.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{quote(ticker, safe='')}?type=trailingDilutedEPS&period1={int((now - timedelta(days=600)).timestamp())}&period2={int(now.timestamp())}"
                try:
                    progress("valuation_request", "正在读取供应商 TTM EPS 并保留原始响应。")
                    response = self.client.get(
                        url, timeout=httpx.Timeout(bounded_timeout(20), connect=bounded_timeout(8))
                    )
                    response.raise_for_status()
                    parsed = parse_eps(response.content, ticker, now, url)
                    self._save(ticker, parsed, response.content)
                    return parsed
                except (httpx.HTTPError, ValueError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        if self.rate_limit:
                            self.rate_limit(ticker, exc.response.headers)
                        break  # The second hostname belongs to the same supplier quota.
                    continue
            if cached and cached[0].retrieved_at <= now:
                return cached[0].model_copy(update={"stale": True})
            pause = self.availability(ticker) if self.availability else None
            if pause and pause["cause"] == "rate_limited":
                raise ValueError(pause["message"])
            raise ValueError(
                "TTM EPS 数据暂时不可用。请重试，或先查询年度 EPS 市盈率、利润和现金流；供应商接口可能限流或不覆盖此股票。"
            )


def provider_pe(tools, inputs):
    if not all(inputs) or [e["kind"] for e in inputs] != ["market", "valuation"]:
        raise ValueError("TTM P/E requires observed price and supplier EPS snapshots")
    price, eps = (
        MarketEvidence.model_validate(inputs[0]).snapshot,
        ValuationEvidence.model_validate(inputs[1]).snapshot,
    )
    if (
        price.ticker != eps.ticker
        or price.currency != eps.currency
        or price.instrument_type != "EQUITY"
    ):
        raise ValueError("TTM P/E requires the same equity and currency")
    if eps.value <= 0:
        raise ValueError("TTM 稀释 EPS 为零或负值，市盈率不适用；可查看收入、利润率和现金流。")
    quote_day = datetime.fromtimestamp(price.quote_at, ZoneInfo(price.exchange_timezone)).date()
    if (
        price.quote_type != "provider_quote"
        or price.stale
        or eps.stale
        or price.as_of > tools.as_of
        or eps.retrieved_at.date() != tools.as_of
        or price.retrieved_at.astimezone(eps.retrieved_at.tzinfo).date() != tools.as_of
        or not 0 <= (tools.as_of - quote_day).days <= 7
        or not 0 <= (tools.as_of - eps.period_end_label).days <= 200
        or abs((price.retrieved_at - eps.retrieved_at).total_seconds()) > 1800
    ):
        raise ValueError(
            "TTM 市盈率需要同日获取的有效报价和 EPS；历史、过期或陈旧缓存不能充当当前估值。请刷新重试。"
        )
    if any(day > eps.period_end_label for day in price.split_dates):
        raise ValueError("EPS 期末标签之后发生拆股，供应商每股口径尚需核对；暂不计算 TTM 市盈率。")
    result = Calculation(
        id=stable_id("calc", "provider_ttm_pe", *[e["id"] for e in inputs]),
        operation="provider_ttm_pe",
        input_ids=[e["id"] for e in inputs],
        value=price.quote_value / eps.value,
        unit="倍",
        label="供应商TTM口径市盈率",
        formula="provider quote / supplier trailing diluted EPS",
        ticker=eps.ticker,
        fiscal_year=eps.period_end_label.year,
    ).model_dump(mode="json")
    result.update(kind="calculation", inputs=inputs)
    tools.ledger[result["id"]] = result
    return {"evidence": result}


def render_valuation(item):
    if item["kind"] == "valuation":
        s = ValuationEvidence.model_validate(item).snapshot
        text = f"{s.ticker} 供应商 TTM 稀释 EPS：{s.value:.2f} {s.currency}/股。供应商期末标签：{s.period_end_label}。"
        if s.stale:
            text += "当前更新失败，显示原获取时间的缓存，不据此计算当前市盈率。"
    else:
        s = ValuationEvidence.model_validate(item["inputs"][1]).snapshot
        text = f"{s.ticker} 供应商 TTM 口径市盈率：{Decimal(item['value']):.2f} 倍。供应商 EPS 期末标签：{s.period_end_label}。"
    return text + f"EPS 获取时间：{s.retrieved_at.isoformat()}。" + NOTE
