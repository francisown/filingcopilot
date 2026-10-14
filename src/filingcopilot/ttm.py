"""Trailing flow calculations hide fiscal-calendar and source-alignment checks."""

import re
from datetime import date, timedelta
from decimal import Decimal, localcontext

from .models import Calculation
from .periods import comparable_quarters, evidence_period, fact_inputs

ADDITIVE_METRICS = (
    "revenue",
    "gross_profit",
    "cost_of_revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "cash_capex",
    "ppe_sale_incentive_proceeds",
)
TTM_NOTE = "TTM 使用年报＋本年累计－上年同期累计，按原始财年日期拼接；52/53 周财年以实际起止日期为准。不同报告间会计口径或重述仍需人工核对。EPS、余额和利润率不能直接套用此公式。"


def is_ttm_question(question: str) -> bool:
    return bool(
        re.search(
            r"(?<![A-Za-z0-9_])TTM(?![A-Za-z0-9_])|滚动|(?:过去|连续|最近).{0,3}(?:十二|12)个月",
            question,
            re.I,
        )
    )


def ttm_period(inputs: list[dict]) -> tuple[str, str]:
    """Validate additive homogeneous inputs and return the derived fiscal window."""
    if len(inputs) == 1 and inputs[0]:
        item = inputs[0]
        if not (
            item["kind"] == "fact"
            and item["metric"] in ADDITIVE_METRICS
            or item["kind"] == "calculation"
            and item["operation"] == "free_cash_flow"
        ):
            raise ValueError("TTM requires an additive flow metric")
        base = fact_inputs(item)[0]
        if (
            not base.get("start")
            or evidence_period(base) != "annual"
            or not (
                357
                <= (date.fromisoformat(base["end"]) - date.fromisoformat(base["start"])).days
                <= 373
            )
        ):
            raise ValueError("TTM annual source must cover a fiscal twelve-month window")
        return base["start"], base["end"]
    if len(inputs) != 3 or any(not e for e in inputs):
        raise ValueError("TTM needs annual, current YTD and prior YTD observed inputs")
    annual, current, previous = inputs
    if len({(e["ticker"], e["unit"], e["kind"]) for e in inputs}) != 1:
        raise ValueError("TTM inputs require one company, currency and evidence type")
    if annual["kind"] == "fact":
        if annual["metric"] not in ADDITIVE_METRICS or len({e["metric"] for e in inputs}) != 1:
            raise ValueError(
                "TTM requires one additive flow metric; EPS and balances are not additive"
            )
    elif annual["kind"] == "calculation":
        if (
            any(e["operation"] != "free_cash_flow" for e in inputs)
            or len({(e["label"], e["formula"]) for e in inputs}) != 1
        ):
            raise ValueError("TTM cash flow requires identical disclosed cash-flow formulas")
    else:
        raise ValueError("TTM requires observed flow facts or cash-flow calculations")
    bases = [fact_inputs(e)[0] for e in inputs]
    a, c, p = bases
    if (
        evidence_period(a) != "annual"
        or not all(e.get("start") for e in bases)
        or not comparable_quarters(c, p)
        or c["filing_id"] != p["filing_id"]
        or a["start"] != p["start"]
        or date.fromisoformat(a["end"]) + timedelta(days=1) != date.fromisoformat(c["start"])
        or not a["start"] <= p["end"] < a["end"] < c["end"]
    ):
        raise ValueError(
            "TTM needs contiguous fiscal periods and same-filing comparable YTD inputs"
        )
    start = date.fromisoformat(p["end"]) + timedelta(days=1)
    end = date.fromisoformat(c["end"])
    if not 357 <= (end - start).days <= 373:
        raise ValueError("TTM source dates do not cover a fiscal twelve-month window")
    return start.isoformat(), end.isoformat()


def trailing_calculation(tools, inputs: list[dict]) -> dict:
    _, end = ttm_period(inputs)
    if any(
        date.fromisoformat(f["filing"]["filed"]) > tools.as_of
        or date.fromisoformat(f["end"]) > tools.as_of
        for item in inputs
        for f in fact_inputs(item)
    ):
        raise ValueError("TTM source is beyond the knowledge date")
    from .tools import stable_id

    with localcontext() as context:
        context.prec = 28
        value = Decimal(inputs[0]["value"])
        if len(inputs) == 3:
            value += Decimal(inputs[1]["value"]) - Decimal(inputs[2]["value"])
    ids = [e["id"] for e in inputs]
    result = Calculation(
        id=stable_id("calc", "trailing_sum", *ids),
        operation="trailing_sum",
        input_ids=ids,
        value=value,
        unit=inputs[0]["unit"],
        label=inputs[0]["label"],
        formula="annual + current_ytd - prior_ytd"
        if len(inputs) == 3
        else "latest disclosed annual flow",
        ticker=inputs[0]["ticker"],
        fiscal_year=int(end[:4]),
    ).model_dump(mode="json")
    result.update(kind="calculation", inputs=inputs)
    tools.ledger[result["id"]] = result
    return {"evidence": result}


def trailing_window(item: dict) -> tuple[str, str] | None:
    """Return the exact derived window for a trailing flow or aligned margin."""
    if item.get("operation") == "trailing_sum":
        return ttm_period(item["inputs"])
    if item.get("operation") in ("ratio", "net_cash_capex") and len(item.get("inputs", [])) == 2:
        left, right = item["inputs"]
        if all(e.get("operation") == "trailing_sum" for e in (left, right)):
            periods = [ttm_period(e["inputs"]) for e in (left, right)]
            if periods[0] != periods[1]:
                raise ValueError("TTM components require one exact trailing window")
            return periods[0]
    return None


def trailing_margin_calculation(tools, inputs: list[dict]) -> dict:
    """Divide aligned trailing flows, never sum quarterly margin percentages."""
    if len(inputs) != 2 or any(not e or e.get("operation") != "trailing_sum" for e in inputs):
        raise ValueError("TTM margin needs two source-bound trailing flow calculations")
    for item in inputs:
        if any(e.get("kind") != "fact" for e in item["inputs"]):
            raise ValueError("TTM margin inputs must be additive financial flow facts")
        if trailing_calculation(tools, item["inputs"])["evidence"] != item:
            raise ValueError("TTM margin input differs from its source-bound calculation")
    left, right = inputs
    if len(left["inputs"]) != len(right["inputs"]):
        raise ValueError("TTM margin requires matching source decompositions")
    for numerator, denominator in zip(left["inputs"], right["inputs"]):
        keys = ("ticker", "unit", "start", "end", "filing_id", "filing")
        if any(numerator[k] != denominator[k] for k in keys):
            raise ValueError("TTM margin requires matching company, currency, periods and filings")
    metric, denominator_metric = left["inputs"][0]["metric"], right["inputs"][0]["metric"]
    ratio = next(
        (
            r
            for r in tools.domain.ratios.values()
            if (r.numerator, r.denominator) == (metric, denominator_metric)
        ),
        None,
    )
    if ratio is None or Decimal(right["value"]) <= 0:
        raise ValueError("TTM margin needs a configured ratio and positive revenue")
    _, end = trailing_window({"operation": "ratio", "inputs": inputs})
    from .tools import stable_id

    with localcontext() as context:
        context.prec = 28
        value = Decimal(left["value"]) / Decimal(right["value"]) * 100
    ids = [e["id"] for e in inputs]
    result = Calculation(
        id=stable_id("calc", "ratio", *ids),
        operation="ratio",
        input_ids=ids,
        value=value,
        unit="%",
        label=ratio.label,
        formula=f"TTM {metric} / TTM {denominator_metric} × 100",
        ticker=left["ticker"],
        fiscal_year=int(end[:4]),
    ).model_dump(mode="json")
    result.update(kind="calculation", inputs=inputs)
    tools.ledger[result["id"]] = result
    return {"evidence": result}


def financial_ttm_margin(tools, ticker: str, ratio: str) -> dict:
    definition = tools.domain.ratios.get(ratio)
    if definition is None:
        raise ValueError("Unknown financial margin")
    flows = [
        financial_ttm(tools, ticker, metric)
        for metric in (definition.numerator, definition.denominator)
    ]
    warnings = list(dict.fromkeys(w for f in flows for w in f.get("warnings", [])))
    reasons = [
        tools.domain.metrics[metric].label + "：" + flow["reason"]
        for metric, flow in zip((definition.numerator, definition.denominator), flows)
        if not flow["found"]
    ]
    if reasons:
        return {
            "found": False,
            "reason": f"TTM {definition.label}缺少同口径输入；"
            + "；".join(dict.fromkeys(reasons))
            + "可先查询已披露的 TTM 营业利润、净利润或营收，并查看所引申报。",
            "warnings": warnings,
        }
    if Decimal(flows[1]["evidence"]["value"]) <= 0:
        return {
            "found": False,
            "reason": f"TTM {definition.label}未生成：分母收入为零或负值，不能生成有效利润率。请查看收入原始证据。",
            "warnings": warnings,
        }
    try:
        evidence = tools.calculate("ratio", *(f["evidence"]["id"] for f in flows))["evidence"]
    except ValueError:
        return {
            "found": False,
            "reason": f"TTM {definition.label}未生成：利润与收入的期间、币种或申报版本未能匹配。请核对源数据，补齐同口径输入后重试。",
            "warnings": warnings,
        }
    start, end = trailing_window(evidence)
    return {
        "found": True,
        "evidence": evidence,
        "period_scope": "ttm",
        "start": start,
        "end": end,
        "warnings": warnings,
        "note": "利润与收入先分别拼接同一 TTM 窗口，再相除；不累加季度利润率。不同申报间会计口径或重述仍需人工核对。",
    }


def cashflow_ttm(tools, ticker: str) -> dict:
    from .capex import capex_basis
    from .cashflow import DEFINITION_NOTE, cashflow_overview

    latest = cashflow_overview(tools, ticker)
    if not latest["found"]:
        return latest
    if latest["period_scope"] == "annual":
        base = tools.ledger[latest["current_id"]]
        try:
            latest["current_id"] = tools.calculate("trailing_sum", base["id"])["evidence"]["id"]
        except ValueError:
            return {
                "found": False,
                "ticker": ticker,
                "warnings": [],
                "reason": "最近年度披露没有覆盖完整十二个月；不能把财年变更的过渡期间当作 TTM。可查询该年度的实际现金流期间。",
            }
        latest["period_scope"] = "ttm"
        latest["previous_id"] = latest["change_id"] = latest["growth_id"] = None
        latest["component_growth_ids"] = []
        latest["component_ids"] = [
            tools.calculate("trailing_sum", e["id"])["evidence"]["id"] for e in base["inputs"]
        ]
        latest["capex_basis"] = capex_basis(tools, base, component_ids=latest["component_ids"])
        latest["warnings"] = [
            "最近已披露完整十二个月就是下方年报期间；尚无更新累计期间，不延伸到今天。",
            *latest["warnings"],
        ]
        latest["ttm_note"] = TTM_NOTE
        return latest
    quarter = cashflow_overview(tools, ticker, "ytd")
    if not quarter["found"]:
        return quarter
    prior_id = quarter.get("previous_id")
    year = (date.fromisoformat(quarter["start"]) - timedelta(days=1)).year
    annual = cashflow_overview(tools, ticker, "annual", year)
    if not annual["found"] or not prior_id:
        return {
            "found": False,
            "ticker": ticker,
            "warnings": [],
            "reason": "TTM 缺少上一完整财年或同一季报的上年同期现金流；请先查看对应年报和季报现金流量表，也可查询最新年度或累计自由现金流。",
        }
    inputs = [
        tools.ledger[annual["current_id"]],
        tools.ledger[quarter["current_id"]],
        tools.ledger[prior_id],
    ]
    try:
        result = tools.calculate("trailing_sum", *(e["id"] for e in inputs))["evidence"]
    except ValueError as exc:
        return {
            "found": False,
            "ticker": ticker,
            "warnings": [],
            "reason": "TTM 期间或现金流定义不可拼接：" + str(exc),
        }
    component_ids = []
    for parts in zip(*(e["inputs"] for e in inputs)):
        component_ids.append(
            tools.calculate("trailing_sum", *(e["id"] for e in parts))["evidence"]["id"]
        )
    start, end = ttm_period(inputs)
    return {
        "found": True,
        "ticker": ticker,
        "filing": quarter["filing"],
        "period_scope": "ttm",
        "start": start,
        "end": end,
        "current_id": result["id"],
        "previous_id": None,
        "change_id": None,
        "growth_id": None,
        "component_growth_ids": [],
        "component_ids": component_ids,
        "definition": inputs[0]["formula"],
        "definition_note": DEFINITION_NOTE,
        "capex_basis": capex_basis(tools, result, component_ids=component_ids),
        "ttm_note": TTM_NOTE,
        "warnings": [],
    }


def financial_ttm(tools, ticker: str, metric: str) -> dict:
    if metric not in ADDITIVE_METRICS or metric not in tools.domain.metrics:
        raise ValueError(
            "TTM supports additive flow metrics; diluted EPS and margins need a separate basis"
        )
    ticker = ticker.upper()
    if tools.fact_loader:
        tools.fact_loader(ticker)
    filings = tools.corpus.catalog(tools.as_of, ticker)
    filings = [f for f in filings if f["form"] in ("10-K", "10-K/A", "10-Q", "10-Q/A")]
    if not filings:
        return {"found": False, "reason": "TTM 需要已公开年报与累计季报；请先准备 SEC 报告。"}
    end = max(f["period_end"] for f in filings)
    filing = next(f for f in filings if f["period_end"] == end)
    if filing["form"].startswith("10-K"):
        result = tools.get_financial_fact(ticker, metric, int(end[:4]))
        if result["found"]:
            try:
                result = {
                    "found": True,
                    **tools.calculate("trailing_sum", result["evidence"]["id"]),
                }
            except ValueError:
                return {
                    "found": False,
                    "reason": "最近年报属于不足或超过完整十二个月的过渡期间；请查询其实际年度期间，不作为 TTM。",
                }
        return {
            **result,
            "period_scope": "ttm",
            "note": TTM_NOTE,
            "warnings": ["最近完整十二个月为下方年度期间；尚无更新累计披露，不延伸到今天。"],
        }
    rows = [
        tools.corpus.evidence(f.id, tools.as_of)
        for year in (int(end[:4]), int(end[:4]) - 1)
        for f in tools.corpus.facts(ticker, metric, year, tools.as_of, forms=("10-Q", "10-Q/A"))
        if f.filing_id == filing["id"]
    ]
    candidates = [
        e for e in rows if e["end"] == end and evidence_period(e) in ("quarter", "ytd6", "ytd9")
    ]
    # Choose cumulative income rather than a single Q2/Q3 duration.
    starts = [e["start"] for e in candidates if e.get("start")]
    current = [e for e in candidates if e["start"] == min(starts)] if starts else []
    if len({(e["start"], e["end"], e["unit"], e["value"]) for e in current}) != 1:
        return {"found": False, "reason": "TTM 缺少唯一的本年累计事实；不能以单季度或零替代。"}
    c = current[0]
    previous = [e for e in rows if comparable_quarters(c, e)]
    if len({(e["start"], e["end"], e["unit"], e["value"]) for e in previous}) != 1:
        return {"found": False, "reason": "TTM 缺少同一季报的唯一上年同期累计事实。"}
    a = tools.get_financial_fact(
        ticker, metric, (date.fromisoformat(c["start"]) - timedelta(days=1)).year
    )
    if not a["found"]:
        return a
    for e in [c, previous[0]]:
        tools.ledger[e["id"]] = e
    try:
        result = tools.calculate("trailing_sum", a["evidence"]["id"], c["id"], previous[0]["id"])
    except ValueError as exc:
        return {"found": False, "reason": "TTM 原始期间不可拼接：" + str(exc)}
    return {"found": True, **result, "period_scope": "ttm", "note": TTM_NOTE, "warnings": []}
