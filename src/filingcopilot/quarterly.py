"""Latest disclosure context; quarter income and cumulative cash flow stay distinct."""

import re
from datetime import date
from decimal import Decimal

from .periods import comparable_quarters, evidence_period, fact_inputs
from .quarter_labels import source_quarter_labels


def is_quarterly_question(question: str) -> bool:
    return bool(
        re.search(
            r"季报|季度|单季|财季|半年|年内累计|10-Q|\bquarter\w*\b|(?<![A-Za-z0-9_])YTD(?![A-Za-z0-9_])|\bQ[1-4]\b|(?:FY)?20\d{2}\s*Q[1-4]",
            question,
            re.I,
        )
    )


def stock_request_scope(question: str) -> dict:
    """Explicit request scope, separate from the scope of available supporting data."""
    match = re.search(
        r"(?P<quarter>单季|财季|季度|季报|10-Q|\bquarter\w*\b|\bQ[1-4]\b)"
        r"|(?P<ytd>年内累计|半年|(?<![A-Za-z0-9_])YTD(?![A-Za-z0-9_]))"
        r"|(?P<ttm>(?<![A-Za-z0-9_])TTM(?![A-Za-z0-9_])|滚动十二个月|连续十二个月)"
        r"|(?P<annual>年度|全年|\bannual\b|(?<![A-Za-z0-9_])FY\s*20\d{2}(?![A-Za-z0-9_]))",
        question,
        re.I,
    )
    return {"primary": match.lastgroup if match else "annual", "explicit": bool(match)}


def requested_fiscal_quarter(question: str) -> tuple[int, int] | None:
    """Parse an explicitly named fiscal quarter; calendar dates remain source truth."""
    match = re.search(
        r"(?:FY\s*)?(?P<year>20\d{2})\s*Q(?P<quarter>[1-4])\b|"
        r"(?P<year_cn>20\d{2})\s*年\s*(?P<quarter_cn>[一二三四1-4])\s*(?:季度|财季)",
        question,
        re.I,
    )
    if not match:
        return None
    quarter = match.group("quarter") or match.group("quarter_cn")
    quarter = {"一": "1", "二": "2", "三": "3", "四": "4"}.get(quarter, quarter)
    return int(match.group("year") or match.group("year_cn")), int(quarter)


def fiscal_quarter_period_end(tools, ticker: str, fiscal_year: int, quarter: int) -> str | None:
    """Resolve Q1–Q3 from an issuer-disclosed label; Q4 is an annual period."""
    if quarter not in (1, 2, 3):
        return None
    filings = [
        filing
        for filing in tools.corpus.catalog(tools.as_of, ticker.upper())
        if filing["form"] in ("10-Q", "10-Q/A")
        and int(filing["period_end"][:4]) in (fiscal_year - 1, fiscal_year, fiscal_year + 1)
    ]
    for filing in filings:
        passages = [
            tools.corpus.evidence(item.id, tools.as_of)
            for item in tools.corpus.passages(tools.as_of, ticker.upper(), filing_id=filing["id"])
        ]
        passages = [item for item in passages if item is not None]
        labels = source_quarter_labels(passages)
        explicit = [
            label
            for label in labels
            if label["year_label"] == fiscal_year and label["quarter"] == quarter
        ]
        if explicit:
            return explicit[0]["end"]
    # Dates alone cannot identify a fiscal quarter.  Do not silently map an
    # ordered calendar-period list to Q1/Q2/Q3.
    return None


def covers_requested_quarter(claims: list[dict], tools, period_end: str) -> bool:
    """Coverage of accepted analysis, not another semantic approval of its wording."""
    return any(
        e.get("metric")
        in {"revenue", "gross_profit", "operating_income", "net_income", "diluted_eps"}
        and e.get("end") == period_end
        and evidence_period(e) == "quarter"
        for claim in claims
        if claim["kind"] == "analysis"
        for identifier in claim["evidence_ids"]
        for e in fact_inputs(tools.ledger[identifier])
    )


def quarterly_overview(
    tools,
    ticker: str,
    period: str = "quarter",
    cash_period: str = "ytd",
    metrics: set[str] | None = None,
    period_end: str | None = None,
) -> dict:
    if period not in ("quarter", "ytd"):
        raise ValueError("Quarterly scope must be quarter or ytd")
    filings = [
        f
        for f in tools.corpus.catalog(tools.as_of, ticker.upper())
        if f["form"] in ("10-Q", "10-Q/A")
    ]
    if not filings:
        return {"found": False, "ticker": ticker, "missing": ["知悉日期前没有可用 10-Q 季报。"]}
    # A recent amendment of an older quarter must not displace the latest report period.
    available_ends = {f["period_end"] for f in filings}
    end = period_end or max(available_ends)
    if end not in available_ends:
        return {
            "found": False,
            "ticker": ticker,
            "period_end": end,
            "missing": [f"知悉日期前没有期末为 {end} 的 10-Q；未用最新季度替代指定期间。"],
        }
    latest = next(f for f in filings if f["period_end"] == end)
    observed, calculations, missing, coverage_warnings = [], [], [], []
    previous_values = {}
    annuals = [
        f for f in tools.corpus.catalog(tools.as_of, ticker.upper()) if f["form"].startswith("10-K")
    ]
    if annuals and max(f["period_end"] for f in annuals) > end:
        coverage_warnings.append(
            "最近年度申报的期末晚于该 10-Q；这里展示最新可用 10-Q，年报第四季度尚未单独提取。可另查最新年度财务。"
        )
    requested = metrics or {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "diluted_eps",
        "operating_cash_flow",
    }
    for metric in sorted(requested):
        scope = cash_period if metric == "operating_cash_flow" else period
        result = tools.get_quarterly_fact(ticker, metric, end, scope)
        if not result["found"] and metric == "operating_cash_flow" and period == "quarter":
            alternative = "quarter" if scope == "ytd" else "ytd"
            result = tools.get_quarterly_fact(ticker, metric, end, alternative)
            if result["found"] and scope == "quarter":
                coverage_warnings.append(
                    "该季报未披露可提取的单季度经营现金流；下方明确列出年内累计数据，不能当作单季度数。"
                )
        if not result["found"]:
            missing.append(result["reason"])
            continue
        current = result["evidence"]
        observed.append(current)
        # Prefer the same filing's comparative dates, including 52/53-week calendars.
        facts = tools.corpus.facts(
            ticker.upper(),
            metric,
            date.fromisoformat(end).year - 1,
            tools.as_of,
            forms=("10-Q", "10-Q/A"),
        )
        prior = [tools.corpus.evidence(f.id, tools.as_of) for f in facts]
        prior = [
            e
            for e in prior
            if e["filing_id"] == current["filing_id"] and comparable_quarters(current, e)
        ]
        if len({(e["start"], e["end"], e["value"], e["unit"]) for e in prior}) == 1:
            previous = prior[0]
            tools.ledger[previous["id"]] = previous
            previous_values[metric] = previous
            try:
                calculations.append(
                    tools.calculate("growth", current["id"], previous["id"])["evidence"]
                )
            except ValueError as exc:
                missing.append(f"{metric} 同比：{exc}")
        else:
            missing.append(f"{metric} 同比缺少同口径上年期间。")
    values = {e["metric"]: e for e in observed}
    for ratio in tools.domain.ratios.values():
        if not {ratio.numerator, ratio.denominator} <= values.keys():
            continue
        try:
            calculations.append(
                tools.calculate(
                    "ratio", values[ratio.numerator]["id"], values[ratio.denominator]["id"]
                )["evidence"]
            )
        except ValueError as exc:
            missing.append(str(exc))
        if {ratio.numerator, ratio.denominator} <= previous_values.keys():
            try:
                calculations.append(
                    tools.calculate(
                        "ratio",
                        previous_values[ratio.numerator]["id"],
                        previous_values[ratio.denominator]["id"],
                    )["evidence"]
                )
            except ValueError as exc:
                missing.append(f"{ratio.label} 上年同期间：{exc}")
    keys = (
        "id",
        "kind",
        "ticker",
        "metric",
        "label",
        "value",
        "unit",
        "start",
        "end",
        "operation",
        "input_ids",
    )
    return {
        "found": bool(observed),
        "ticker": ticker,
        "period_end": end,
        "period_scope": period,
        "filing": latest,
        "evidence": [{k: e[k] for k in keys if k in e} for e in [*observed, *calculations]],
        "comparisons": _comparisons(calculations, period),
        "missing": missing,
        "coverage_warnings": coverage_warnings,
        "note": "Income/EPS use the requested source-disclosed quarter or YTD scope. Cash flow is separately labeled with exact dates. No quarter is derived by subtracting YTD, and no annual figure is used as a quarter.",
    }


def _comparisons(calculations: list[dict], period: str) -> list[dict]:
    """Narrow descriptive drafts with complete citations; ordinary verification still applies."""
    scope = "单季度" if period == "quarter" else "年内累计"
    comparisons = []
    for item in calculations:
        current = item["inputs"][0]
        if current.get("metric") not in {
            "revenue",
            "operating_income",
            "net_income",
            "diluted_eps",
        }:
            continue
        if item["operation"] == "growth":
            trend = (
                "增加"
                if Decimal(item["value"]) > 0
                else "减少"
                if Decimal(item["value"]) < 0
                else "持平"
            )
            comparisons.append(
                {
                    "statement": f"{current['start']} 至 {current['end']} {scope}，{current['label']}同比{trend}；持续性仍需后续披露验证。",
                    "evidence_ids": [item["id"]],
                }
            )
    ratios = [e for e in calculations if e["operation"] == "ratio"]
    for current in ratios:
        fact = current["inputs"][0]
        prior = next(
            (
                e
                for e in ratios
                if e["label"] == current["label"] and comparable_quarters(fact, e["inputs"][0])
            ),
            None,
        )
        if prior is None:
            continue
        delta = Decimal(current["value"]) - Decimal(prior["value"])
        trend = "上升" if delta > 0 else "下降" if delta < 0 else "持平"
        comparisons.append(
            {
                "statement": f"{fact['start']} 至 {fact['end']} {scope}，{current['label']}同比{trend}；变化原因仍需原文证据。",
                "evidence_ids": [current["id"], prior["id"]],
            }
        )
    return comparisons
