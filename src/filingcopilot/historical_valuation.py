"""Daily price / the annual EPS publicly available on that date.

This is an annual denominator reference, not historical TTM, forward P/E or an
as-traded price reconstruction. It never rewrites an EPS for a stock split.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from .market import MarketEvidence, symbol
from .models import StrictModel
from .periods import evidence_period

SCHEMA = "filingcopilot.historical-annual-pe/v1"
NOTE = (
    "历史年度 EPS 口径：每日 Close 除以此前已公开的最新完整年度稀释 EPS，非 TTM、非预测市盈率。"
    "申报只给公开日，保守从次日启用；之后年报的比较值不会回填更早日期。"
    "Close 为供应商当前拆股口径，不含现金股息复权；如果 EPS 公开后有拆股，未对齐股份基础的点留空。"
    "亏损、零 EPS、超过 550 日未更新年度 EPS、币种或股份类别不符均不计算。"
    "分位数仅覆盖实际有效点，不证明估值合理，也不保证历史资料齐全。"
)


class HistoricalValuationRequest(StrictModel):
    ticker: str = Field(min_length=1, max_length=10)
    period: Literal["1Y", "5Y", "MAX"] = "5Y"
    as_of: date = Field(default_factory=date.today)
    refresh_sources: bool = True

    @field_validator("ticker")
    @classmethod
    def canonical(cls, value):
        return symbol(value)

    @field_validator("as_of")
    @classmethod
    def current_only(cls, value):
        if value != date.today():
            raise ValueError("历史估值参照目前由今天的供应商历史日线重建；请把知悉日期设为今天。")
        return value


def is_historical_valuation_question(question: str) -> bool:
    return bool(
        re.search(r"市盈率|P/?E|估值倍数", question, re.I)
        and re.search(r"历史|区间|历年|过去.{0,3}年|historical|history|range", question, re.I)
        and not re.search(r"同行|同业|peer|预测|未来|forward|forecast|TTM", question, re.I)
    )


def _annual_eps(item: dict, ticker: str) -> bool:
    return (
        item.get("kind") == "fact"
        and item.get("ticker") == ticker
        and item.get("metric") == "diluted_eps"
        and item.get("filing", {}).get("form") in ("10-K", "10-K/A", "20-F", "20-F/A")
        and evidence_period(item) == "annual"
    )


def historical_reference(request, market, eps_items) -> dict:
    snapshot = MarketEvidence.model_validate(market).snapshot
    if snapshot.ticker != request.ticker or snapshot.period != request.period:
        raise ValueError("Historical reference market identity or period mismatch")
    if snapshot.as_of > request.as_of:
        raise ValueError("Historical reference exceeds knowledge date")
    if any(not _annual_eps(e, request.ticker) for e in eps_items):
        raise ValueError("Historical reference needs US complete annual diluted EPS")
    eps_items = sorted(eps_items, key=lambda e: e["id"])
    points, missing = [], {}
    for point in snapshot.points:
        candidates = [e for e in eps_items if date.fromisoformat(e["filing"]["filed"]) < point.date]
        eps = None
        reason = None
        if snapshot.instrument_type != "EQUITY" or snapshot.basis == "split_adjusted_close":
            reason = "share_basis_unavailable"
        elif not candidates:
            reason = "no_public_eps"
        else:
            latest = max((e["end"], e["filing"]["filed"]) for e in candidates)
            same = [e for e in candidates if (e["end"], e["filing"]["filed"]) == latest]
            if len({(e["value"], e["unit"], e["start"], e["end"]) for e in same}) > 1:
                reason = "conflicting_eps"
            else:
                eps = sorted(same, key=lambda e: e["id"])[0]
                filed = date.fromisoformat(eps["filing"]["filed"])
                if eps["unit"] != snapshot.currency + "/shares":
                    reason = "currency_or_share_mismatch"
                elif Decimal(eps["value"]) <= 0:
                    reason = "nonpositive_eps"
                elif point.date - filed > timedelta(days=550):
                    reason = "stale_annual_eps"
                elif any(day > filed for day in snapshot.split_dates):
                    # Yahoo Close uses today's share basis, including later splits.
                    # Without a source-bound ratio bridge, do not apply raw old EPS.
                    reason = "split_basis_unmatched"
        value = str(point.close / Decimal(eps["value"])) if not reason else None
        if reason:
            missing[reason] = missing.get(reason, 0) + 1
        points.append(
            {
                "date": point.date.isoformat(),
                "close": str(point.close),
                "eps_id": eps["id"] if eps else None,
                "pe": value,
                "missing": reason,
            }
        )
    valid = sorted(Decimal(p["pe"]) for p in points if p["pe"] is not None)

    def percentile(part):
        if not valid:
            return None
        rank = max(
            0, int((len(valid) * Decimal(part)).to_integral_value(rounding="ROUND_CEILING")) - 1
        )
        return str(valid[rank])

    return {
        "schema_version": SCHEMA,
        "ticker": request.ticker,
        "as_of": request.as_of.isoformat(),
        "period": request.period,
        "market_id": market["id"],
        "eps_ids": [e["id"] for e in eps_items],
        "currency": snapshot.currency,
        "price_basis": "supplier_close_current_split_basis",
        "eps_basis": "public_annual_diluted_eps",
        "start": points[0]["date"],
        "end": points[-1]["date"],
        "trading_days": len(points),
        "valid_days": len(valid),
        "missing_counts": missing,
        "statistics": {
            "low": str(valid[0]) if valid else None,
            "p25": percentile("0.25"),
            "median": percentile("0.50"),
            "p75": percentile("0.75"),
            "high": str(valid[-1]) if valid else None,
        },
        "points": points,
        "note": NOTE,
    }


def collect_historical_reference(tools, request) -> dict:
    observed = tools.get_stock_market(request.ticker, request.period)
    if not observed.get("found"):
        raise ValueError(observed.get("reason", "历史价格来源获取未完成。"))
    market = tools.ledger[observed["evidence_id"]]
    items = {}
    years = {int(f["period_end"][:4]) for f in tools.corpus.catalog(request.as_of, request.ticker)}
    if years:
        for year in range(min(years) - 2, max(years) + 1):
            for fact in tools.corpus.facts(request.ticker, "diluted_eps", year, request.as_of):
                e = tools.corpus.evidence(fact.id, request.as_of)
                if _annual_eps(e, request.ticker):
                    tools.ledger[e["id"]] = e
                    items[e["id"]] = e
    return historical_reference(request, market, list(items.values()))


def validate_historical_reference(result: dict, ledger: dict, as_of: date) -> None:
    # Do not validate saved dates against today's clock: frozen packages replay forever.
    request = HistoricalValuationRequest.model_construct(
        ticker=symbol(result["ticker"]), period=result["period"], as_of=as_of
    )
    if result["period"] not in ("1Y", "5Y", "MAX") or result["as_of"] != as_of.isoformat():
        raise ValueError("Historical reference scope mismatch")
    eps_items = [e for e in ledger.values() if _annual_eps(e, request.ticker)]
    expected = historical_reference(request, ledger[result["market_id"]], eps_items)
    if result != expected:
        raise ValueError(
            "Historical EPS availability, price, gaps or percentiles differ from sources"
        )
