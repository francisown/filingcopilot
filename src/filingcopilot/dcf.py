"""Deterministic DCF sensitivity arithmetic with facts separated from assumptions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

from pydantic import Field

from .models import StrictModel


class DcfRequest(StrictModel):
    ticker: str = Field(min_length=1, max_length=10)
    as_of: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    base_free_cash_flow: Decimal = Field(gt=0, allow_inf_nan=False)
    shares_outstanding: Decimal = Field(gt=0, allow_inf_nan=False)
    forecast_years: int = Field(default=5, ge=1, le=10, strict=True)
    growth_pct: Decimal = Field(ge=-50, le=100, allow_inf_nan=False)
    discount_pct: Decimal = Field(gt=0, le=100, allow_inf_nan=False)
    terminal_growth_pct: Decimal = Field(ge=-20, le=20, allow_inf_nan=False)
    growth_grid: list[Decimal] = Field(
        default_factory=lambda: [Decimal("-5"), Decimal("0"), Decimal("5")],
        min_length=2,
        max_length=7,
    )
    discount_grid: list[Decimal] = Field(
        default_factory=lambda: [Decimal("8"), Decimal("10"), Decimal("12")],
        min_length=2,
        max_length=7,
    )
    source_evidence_ids: list[str] = Field(min_length=1, max_length=8)


def _per_share(request: DcfRequest, growth: Decimal, discount: Decimal) -> dict:
    if discount <= request.terminal_growth_pct:
        raise ValueError("DCF 折现率必须高于终值增长率。")
    with localcontext() as ctx:
        ctx.prec = 40
        base = request.base_free_cash_flow
        rate = discount / 100
        growth_rate = growth / 100
        terminal_growth = request.terminal_growth_pct / 100
        present_value = sum(
            base * (1 + growth_rate) ** year / (1 + rate) ** year
            for year in range(1, request.forecast_years + 1)
        )
        final_fcf = base * (1 + growth_rate) ** request.forecast_years
        terminal_value = final_fcf * (1 + terminal_growth) / (rate - terminal_growth)
        cashflow_present_value = (
            present_value + terminal_value / (1 + rate) ** request.forecast_years
        )
        per_share = cashflow_present_value / request.shares_outstanding
    return {
        "growth_pct": str(growth),
        "discount_pct": str(discount),
        "present_value_forecast": str(present_value),
        "terminal_value": str(terminal_value),
        "cashflow_present_value": str(cashflow_present_value),
        "implied_value_per_share": str(per_share),
    }


def dcf_sensitivity(request: DcfRequest) -> dict:
    if request.as_of > date.today():
        raise ValueError("DCF 知悉日期不能晚于今天。")
    if any(not value.is_finite() or not -50 <= value <= 100 for value in request.growth_grid):
        raise ValueError("DCF 增长率网格须为有限数，范围为 -50% 至 100%。")
    if any(not value.is_finite() or not 0 < value <= 100 for value in request.discount_grid):
        raise ValueError("DCF 折现率网格须为有限数，范围为 0% 至 100%。")
    for grid in (request.growth_grid, request.discount_grid):
        if len(set(grid)) != len(grid):
            raise ValueError("DCF 敏感性表的假设不能重复。")
    base = _per_share(request, request.growth_pct, request.discount_pct)
    rows = [
        _per_share(request, growth, discount)
        for growth in request.growth_grid
        for discount in request.discount_grid
    ]
    return {
        "schema": "filingcopilot.dcf/v2",
        "ticker": request.ticker.upper(),
        "as_of": request.as_of.isoformat(),
        "currency": request.currency,
        "source_validation": {
            "status": "not_checked",
            "note": "这里只完成算术；来源 ID 与输入金额尚未匹配。",
        },
        "inputs": {
            "base_free_cash_flow": str(request.base_free_cash_flow),
            "shares_outstanding": str(request.shares_outstanding),
            "source_evidence_ids": list(request.source_evidence_ids),
        },
        "user_assumptions": {
            "forecast_years": request.forecast_years,
            "growth_pct": str(request.growth_pct),
            "discount_pct": str(request.discount_pct),
            "terminal_growth_pct": str(request.terminal_growth_pct),
            "growth_grid": [str(value) for value in request.growth_grid],
            "discount_grid": [str(value) for value in request.discount_grid],
        },
        "formula": "PV(FCF_1..FCF_n) + PV(FCF_n*(1+g_terminal)/(discount_rate-g_terminal)); cashflow PV per common share = cashflow PV / common shares",
        "base_case": base,
        "sensitivity": rows,
        "note": "这是依赖用户假设的现金流敏感性分析，不是公司指引、目标价或投资建议。基期 FCF 为经营现金流减现金 PP&E 购置毛额，未另扣融资租赁本金、收购和其他投资；它不是无杠杆企业自由现金流，折现结果不标作企业价值或股权目标价。分母为普通股数量，不直接适用于 ADS 报价。",
    }


def sourced_dcf_sensitivity(service, request: DcfRequest) -> dict:
    """Match each observed input to publicly available, same-issuer annual sources."""
    evidence = []
    for item_id in request.source_evidence_ids:
        item = service.corpus.evidence(item_id, request.as_of)
        if item is None or item.get("ticker") != request.ticker.upper():
            raise ValueError("DCF 输入必须引用知悉日期前、属于所选公司的本地原始证据。")
        evidence.append(item)
    if len({item["id"] for item in evidence}) != len(evidence):
        raise ValueError("DCF 来源 ID 不能重复。")
    matched = {}
    for metric in ("operating_cash_flow", "cash_capex"):
        candidates = [e for e in evidence if e.get("kind") == "fact" and e.get("metric") == metric]
        if len(candidates) != 1:
            raise ValueError("DCF 需要各一项年度经营现金流与现金 PP&E 购置毛额原始事实。")
        matched[metric] = candidates[0]
    cash, capex = matched["operating_cash_flow"], matched["cash_capex"]
    if (
        cash["filing_id"] != capex["filing_id"]
        or (cash["start"], cash["end"]) != (capex["start"], capex["end"])
        or cash["unit"] != request.currency
        or capex["unit"] != request.currency
        or cash["filing"]["form"] not in ("10-K", "10-K/A", "20-F", "20-F/A")
        or not cash.get("start")
        or not 330
        <= (date.fromisoformat(cash["end"]) - date.fromisoformat(cash["start"])).days
        <= 400
        or Decimal(capex["value"]) < 0
        or Decimal(cash["value"]) - Decimal(capex["value"]) != request.base_free_cash_flow
    ):
        raise ValueError(
            "DCF 基期金额须匹配同一年度、申报版本与币种的经营现金流减现金 PP&E 购置毛额。"
        )
    shares = [
        e
        for e in evidence
        if e.get("kind") == "statement_fact"
        and e.get("concept")
        in ("dei:EntityCommonStockSharesOutstanding", "us-gaap:CommonStockSharesOutstanding")
        and e.get("unit") in ("shares", "xbrli:shares")
        and not e.get("dimensions")
        and not e.get("start")
        and e.get("filing_id") == cash["filing_id"]
        and abs((date.fromisoformat(e["end"]) - date.fromisoformat(cash["end"])).days) <= 120
    ]
    if len(shares) != 1 or Decimal(shares[0]["value"]) != request.shares_outstanding:
        raise ValueError(
            "DCF 分母须匹配该年报的普通股在外数量；加权平均股数、分部或 ADS 股数不能替代。"
        )
    required = {cash["id"], capex["id"], shares[0]["id"]}
    if {e["id"] for e in evidence} != required:
        raise ValueError("DCF 来源只应包含基期现金流、现金购置与普通股在外数量三项输入。")
    result = dcf_sensitivity(request)
    result["source_validation"] = {
        "status": "matched_to_source_inputs",
        "evidence_ids": sorted(required),
        "base_period": {"start": cash["start"], "end": cash["end"]},
        "shares_date": shares[0]["end"],
        "note": "代码已匹配来源金额、公司、币种、期间与股份口径；情景假设仍由用户提供。",
    }
    result["evidence"] = evidence
    return result


def dcf_source_inputs(service, ticker: str, as_of: date) -> dict:
    """Offer a scenario only when all three observed inputs can actually be matched."""
    from .market import symbol

    ticker = symbol(ticker)
    if as_of > date.today():
        raise ValueError("DCF 知悉日期不能晚于今天。")
    tools = service.tools(as_of)
    filings = [
        f
        for f in service.corpus.catalog(as_of, ticker)
        if f["form"] in ("10-K", "10-K/A", "20-F", "20-F/A")
    ]

    def missing(reason):
        return {"found": False, "ticker": ticker, "as_of": as_of.isoformat(), "reason": reason}

    if not filings:
        return missing("尚无公开年报。请先检查最新披露，取得年度现金流与普通股数量。")
    year = max(int(f["period_end"][:4]) for f in filings)
    cash = tools.get_financial_fact(ticker, "operating_cash_flow", year)
    capex = tools.get_financial_fact(ticker, "cash_capex", year)
    if not cash.get("found") or not capex.get("found"):
        return missing("该年度尚未取得经营现金流或现金 PP&E 购置毛额；先检查披露或查看报表原文。")
    cash, capex = cash["evidence"], capex["evidence"]
    end = date.fromisoformat(cash["end"])
    rows = service.corpus.statement_facts(ticker, as_of, filing_id=cash["filing_id"])
    shares = [
        r
        for r in rows
        if r.concept
        in ("us-gaap:CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding")
        and r.unit in ("shares", "xbrli:shares")
        and not r.start
        and not r.dimensions
        and abs((r.end - end).days) <= 120
    ]
    if not shares:
        return missing(
            "未取得同一年报的普通股在外数量；不能用加权平均股数或 ADS 比例替代。可继续使用 EPS 估值情景。"
        )
    # Prefer the report-period count; the cover count may be measured weeks later.
    selected = min(
        shares,
        key=lambda r: (
            abs((r.end - end).days),
            r.concept != "us-gaap:CommonStockSharesOutstanding",
            r.id,
        ),
    )
    if (
        len({r.value for r in shares if r.end == selected.end and r.concept == selected.concept})
        > 1
    ):
        return missing("同一时点普通股数量存在冲突，需查看原文，暂不生成现金流情景。")
    fcf = Decimal(cash["value"]) - Decimal(capex["value"])
    if fcf <= 0:
        return missing(
            "该口径基期自由现金流非正数，当前正现金流终值模型不适用。请研究投入与现金流改善路径。"
        )
    request = DcfRequest(
        ticker=ticker,
        as_of=as_of,
        currency=cash["unit"],
        base_free_cash_flow=fcf,
        shares_outstanding=selected.value,
        growth_pct=0,
        discount_pct=10,
        terminal_growth_pct=2,
        source_evidence_ids=[cash["id"], capex["id"], selected.id],
    )
    try:
        matched = sourced_dcf_sensitivity(service, request)
    except ValueError as exc:
        return missing(str(exc))
    return {
        "found": True,
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "currency": request.currency,
        "inputs": matched["inputs"],
        "source_validation": matched["source_validation"],
        "evidence": matched["evidence"],
        "note": matched["note"],
    }
