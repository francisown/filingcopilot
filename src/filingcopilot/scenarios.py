"""Explicit EPS/multiple assumptions with deterministic, auditable arithmetic."""

from __future__ import annotations

import time
import uuid
from datetime import date
from decimal import Decimal, localcontext
from types import SimpleNamespace
from typing import Literal

from pydantic import Field

from .domain import DEFAULT_DOMAIN
from .models import Claim, ResearchAnswer, StrictModel, Submission
from .verification import verify_submission

FORMULA = "EPS × (1 + growth_pct / 100)^years × terminal_pe / (1 + discount_pct / 100)^years"
NOTE = (
    "增长率、未来市盈率及折现率是你输入的假设，不是公司指引或模型预测。"
    "结果为 EPS×倍数的折现情景，不含期间股息、回购及未来股份变化；不是 DCF、目标价或收益保证。"
)
SCHEMA = "filingcopilot.valuation-scenarios/v2"


class ScenarioCase(StrictModel):
    name: str = Field(min_length=1, max_length=24)
    growth_pct: Decimal = Field(ge=-50, le=100, allow_inf_nan=False)
    terminal_pe: Decimal = Field(gt=0, le=100, allow_inf_nan=False)


class ScenarioRequest(StrictModel):
    ticker: str = Field(min_length=1, max_length=10)
    as_of: date = Field(default_factory=date.today)
    basis: Literal["annual", "provider_ttm"] = "annual"
    years: int = Field(default=3, ge=1, le=10, strict=True)
    discount_pct: Decimal = Field(ge=0, le=50, allow_inf_nan=False)
    cases: list[ScenarioCase] = Field(min_length=2, max_length=5)
    language: Literal["zh", "en"] = "en"


def scenario_result(request, eps, market, domain=DEFAULT_DOMAIN, *, price_reference=True):
    if eps["ticker"] != market["ticker"] or eps["ticker"] != request.ticker.upper():
        raise ValueError("EPS 与报价必须属于同一证券。")
    snapshot = market["snapshot"]
    # The current knowledge date can be ahead of the exchange's local trading
    # date (for example shortly after midnight in China). A prior trade day is
    # valid; a quote after the requested cutoff is not.
    if snapshot["as_of"] > request.as_of.isoformat():
        raise ValueError("情景报价的交易日期晚于请求知悉日期。")
    if request.basis == "provider_ttm":
        if eps["kind"] != "valuation" or eps["snapshot"]["stale"]:
            raise ValueError("供应商 TTM 情景需新鲜的当前 EPS；请刷新估值来源。")
        if snapshot["quote_type"] != "provider_quote" or snapshot["stale"]:
            raise ValueError("供应商 TTM 情景需同一供应商的当前报价。")
        if "finance.yahoo.com/" not in snapshot["source_url"]:
            raise ValueError("不能将独立供应商报价与 Yahoo TTM EPS 混合。")
        value, currency = Decimal(eps["snapshot"]["value"]), eps["snapshot"]["currency"]
        period = "供应商 TTM 期末标签 " + eps["snapshot"]["period_end_label"]
    else:
        if eps["kind"] != "fact" or eps["metric"] != "diluted_eps":
            raise ValueError("年度情景目前需要普通股稀释 EPS；ADS 汇率及股份桥接待补齐。")
        value, currency = Decimal(eps["value"]), eps["unit"].removesuffix("/shares")
        if currency == eps["unit"] or eps["filing"]["filed"] > request.as_of.isoformat():
            raise ValueError("EPS 单位或公开日期不符合情景输入。")
        # Reuse the actual annual-P/E share/split checks, not a second relaxed formula.
        from .tools import ResearchTools

        tools = ResearchTools(None, SimpleNamespace(domain=domain), request.as_of)
        tools.ledger = {eps["id"]: eps, market["id"]: market}
        tools.calculate("annual_pe", market["id"], eps["id"])
        period = f"FY{eps['fiscal_year']} {eps['start']} 至 {eps['end']}"
    if value <= 0 or currency != snapshot["currency"]:
        raise ValueError("情景需要正数 EPS，且 EPS 和股价币种一致；亏损不能强套市盈率。")
    if request.basis == "provider_ttm":
        from .tools import ResearchTools

        tools = ResearchTools(None, SimpleNamespace(domain=domain), request.as_of)
        tools.ledger = {eps["id"]: eps, market["id"]: market}
        tools.calculate("provider_ttm_pe", market["id"], eps["id"])
    if len({c.name for c in request.cases}) != len(request.cases):
        raise ValueError("请为每个情景使用不同名称。")
    price = Decimal(snapshot["quote_value"])
    from .market import MarketSnapshot, statistics

    price_stats = statistics(MarketSnapshot.model_validate(snapshot))
    reference = {
        "period": snapshot["period"],
        "basis": snapshot["basis"],
        "start": price_stats["start"],
        "end": price_stats["end"],
        "trading_days": price_stats["trading_days"],
        "low": price_stats["low"],
        "p25": price_stats["p25"],
        "median": price_stats["median"],
        "p75": price_stats["p75"],
        "high": price_stats["high"],
        "last_daily_value": price_stats["last"],
        "note": "历史价格分位来自同一行情快照的实际日线；不是历史市盈率、目标价或估值结论。复权口径见 basis。",
    }
    rows = []
    with localcontext() as ctx:
        ctx.prec = 40
        for case in request.cases:
            projected = value * (1 + case.growth_pct / 100) ** request.years
            terminal = projected * case.terminal_pe
            present = terminal / (1 + request.discount_pct / 100) ** request.years
            rows.append(
                {
                    **case.model_dump(mode="json"),
                    "future_eps": str(projected),
                    "terminal_value": str(terminal),
                    "discounted_value": str(present),
                    "price_difference_pct": str((present / price - 1) * 100),
                }
            )
    result = {
        "request": request.model_dump(mode="json"),
        "eps_id": eps["id"],
        "market_id": market["id"],
        "currency": currency,
        "eps_period": period,
        "base_eps": str(value),
        "observed_price": str(price),
        "formula": FORMULA,
        "rows": rows,
        "note": NOTE,
    }
    if price_reference:
        result.update(schema_version=SCHEMA, price_reference=reference)
    return result


def validate_scenarios(result, ledger, as_of, domain=DEFAULT_DOMAIN):
    request = ScenarioRequest.model_validate(result["request"])
    if request.as_of != as_of:
        raise ValueError("Scenario knowledge date differs from research")
    # Initial 0.48 and the first local price-reference acceptance run had no
    # schema field. Replay their exact shapes; named v2 always requires the
    # reference and still compares every value against the frozen daily data.
    version = result.get("schema_version")
    if "schema_version" in result and version != SCHEMA:
        raise ValueError("Unknown scenario schema")
    with_reference = version == SCHEMA or "price_reference" in result
    expected = scenario_result(
        request,
        ledger[result["eps_id"]],
        ledger[result["market_id"]],
        domain,
        price_reference=with_reference,
    )
    if version is None:
        expected.pop("schema_version", None)
    if result != expected:
        raise ValueError("Scenario assumptions, source identity or arithmetic mismatch")


def valuation_scenarios(service, request):
    started = time.perf_counter()
    if request.as_of > date.today():
        raise ValueError("知悉日期不能晚于今天。")
    tools = service.tools(request.as_of)
    result = (
        tools.get_provider_ttm_valuation(request.ticker)
        if request.basis == "provider_ttm"
        else tools.get_stock_valuation(request.ticker)
    )
    if not result.get("found"):
        raise ValueError(result.get("reason", "估值来源获取未完成；可先查询每股收益或行情。"))
    calculation = tools.ledger[result["evidence"]["id"]]
    if calculation["kind"] != "calculation":
        raise ValueError("取得 EPS 但未取得匹配报价，请先刷新行情。")
    market, eps = calculation["inputs"]
    frozen = scenario_result(request, eps, market, tools.domain)
    claims, errors = verify_submission(
        Submission(
            claims=[
                Claim(kind=e["kind"], evidence_ids=[e["id"]]) for e in (market, eps, calculation)
            ]
        ),
        tools,
        "估值情景输入",
        focus_ticker=request.ticker,
    )
    answer = ResearchAnswer(
        id="run-" + uuid.uuid4().hex,
        question=f"{request.ticker} 每股收益与市盈率估值情景",
        as_of=request.as_of,
        mode="scenarios",
        status="partial" if errors else "answered",
        answer=claims,
        gaps=errors,
        evidence=list(tools.ledger.values()),
        trace=[{"stage": "valuation_scenarios", "result": frozen}],
        usage={"model_calls": 0, "total_tokens": 0},
        elapsed_ms=round((time.perf_counter() - started) * 1000),
        research_type="stock",
    )
    return answer, tools.domain, request.ticker
