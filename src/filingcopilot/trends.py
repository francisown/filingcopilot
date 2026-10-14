"""Annual trend references: explicit fiscal windows, immutable versions, no interpolation."""

import re
from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from .market import symbol
from .models import StrictModel
from .periods import evidence_period

SCHEMA = "filingcopilot.financial-trend/v1"
METRICS = ("revenue", "operating_income", "net_income", "operating_cash_flow", "free_cash_flow")
INPUT_METRICS = (*METRICS[:-1], "cash_capex")
NOTE = (
    "只展示知悉日期前已公开的完整年度，各年保留实际起止日期和申报版本；"
    "每年数字使用同一申报中的同期间事实，自由现金流为经营现金流减现金 PP&E 购置毛额，"
    "未抵减出售或补助收款。缺失年份不补零，不同币种不换算；"
    "后续年报的比较数字可能与原年度报告不同，不自动解释为重述，也不把年度数字当作 TTM。"
)


class TrendRequest(StrictModel):
    ticker: str = Field(min_length=1, max_length=10)
    years: Literal[3, 5] = 5
    as_of: date = Field(default_factory=date.today)
    refresh_sources: bool = True
    language: Literal["zh", "en"] = "en"

    @field_validator("ticker")
    @classmethod
    def listing(cls, value):
        return symbol(value)

    @field_validator("as_of")
    @classmethod
    def no_future(cls, value):
        if value > date.today():
            raise ValueError("知悉日期不能晚于今天。")
        return value


def annual_trend(tools, request: TrendRequest) -> dict:
    """Choose the latest observed filing for each year, then keep all cells in that version."""
    forms = tools.domain.annual_forms(request.ticker)
    filings = [f for f in tools.corpus.catalog(request.as_of, request.ticker) if f["form"] in forms]
    latest = max((int(f["period_end"][:4]) for f in filings), default=None)
    rows = []
    for year in range(latest - request.years + 1, latest + 1) if latest else []:
        observed = {}
        for metric in INPUT_METRICS:
            for fact in tools.corpus.facts(
                request.ticker, metric, year, request.as_of, forms=forms
            ):
                item = tools.corpus.evidence(fact.id, request.as_of)
                if item["start"] and evidence_period(item) == "annual":
                    observed.setdefault(metric, []).append(item)
        # Source ordering is independent of metric availability. Never silently use
        # an earlier report just because the newest report has a conflicting value.
        newest = max(
            (e for candidates in observed.values() for e in candidates),
            key=lambda e: (e["filing"]["filed"], e["filing_id"]),
            default=None,
        )
        chosen, issues = {}, []
        if newest:
            for metric, candidates in observed.items():
                candidates = [e for e in candidates if e["filing_id"] == newest["filing_id"]]
                if not candidates:
                    continue
                if len({(e["start"], e["end"], e["unit"], e["value"]) for e in candidates}) != 1:
                    issues.append(f"{metric} 在同一申报中有冲突，未选择其中一个值。")
                    continue
                chosen[metric] = sorted(candidates, key=lambda e: e["id"])[0]
        basis = next(iter(chosen.values()), None)
        cells = {}
        if basis:
            for metric, item in chosen.items():
                if (item["start"], item["end"]) != (basis["start"], basis["end"]):
                    issues.append(f"{metric} 与该年的实际年度期间不同，未混合。")
                    continue
                tools.ledger[item["id"]] = item
                cells[metric] = item["id"]
            if "operating_cash_flow" in cells and "cash_capex" in cells:
                try:
                    cells["free_cash_flow"] = tools.calculate(
                        "free_cash_flow", cells["operating_cash_flow"], cells["cash_capex"]
                    )["evidence"]["id"]
                except ValueError:
                    issues.append("现金流与现金 PP&E 购置的输入口径未对齐，未计算自由现金流。")
        rows.append(
            {
                "fiscal_year": year,
                "basis_id": basis["id"] if basis else None,
                "cells": cells,
                "missing": [metric for metric in METRICS if metric not in cells],
                "issues": issues,
            }
        )
    return {
        "schema_version": SCHEMA,
        "ticker": request.ticker,
        "years": request.years,
        "as_of": request.as_of.isoformat(),
        "latest_fiscal_year": latest,
        "rows": rows,
        "note": NOTE,
    }


def validate_trend(result: dict, ledger: dict, domain, as_of: date) -> None:
    """Replay cell meaning and window independently; amounts come directly from the ledger."""
    if result["schema_version"] != SCHEMA or result["note"] != NOTE:
        raise ValueError("Unknown financial trend schema")
    request = TrendRequest(ticker=result["ticker"], years=result["years"], as_of=result["as_of"])
    if request.as_of != as_of:
        raise ValueError("Trend knowledge date differs from its research")
    latest = result["latest_fiscal_year"]
    expected = list(range(latest - request.years + 1, latest + 1)) if latest else []
    if [r["fiscal_year"] for r in result["rows"]] != expected:
        raise ValueError("Trend must retain each requested fiscal year, including missing years")
    for row in result["rows"]:
        basis = ledger[row["basis_id"]] if row["basis_id"] else None
        if not basis and row["cells"]:
            raise ValueError("Trend cells require an annual source basis")
        if basis and (
            basis["kind"] != "fact"
            or basis["metric"] not in INPUT_METRICS
            or basis["id"] not in row["cells"].values()
            or basis["ticker"] != request.ticker
            or basis["fiscal_year"] != row["fiscal_year"]
            or not basis["start"]
            or evidence_period(basis) != "annual"
            or basis["filing"]["form"] not in domain.annual_forms(request.ticker)
        ):
            raise ValueError("Trend basis is not the selected company's complete annual period")
        if row["missing"] != [metric for metric in METRICS if metric not in row["cells"]]:
            raise ValueError("Trend missing-data labels differ from observed cells")
        for metric, key in row["cells"].items():
            item = ledger[key]
            if item["ticker"] != request.ticker or item["fiscal_year"] != row["fiscal_year"]:
                raise ValueError("Trend cell company/year mismatch")
            if metric in INPUT_METRICS:
                if (
                    item["kind"] != "fact"
                    or item["metric"] != metric
                    or (item["start"], item["end"], item["filing_id"])
                    != (basis["start"], basis["end"], basis["filing_id"])
                ):
                    raise ValueError("Trend cell metric, period or version mismatch")
            elif metric == "free_cash_flow":
                if item.get("operation") != "free_cash_flow" or item["input_ids"] != [
                    row["cells"].get("operating_cash_flow"),
                    row["cells"].get("cash_capex"),
                ]:
                    raise ValueError("Trend FCF must use same-year gross PP&E purchases")
            else:
                raise ValueError("Unknown financial trend cell")


def is_financial_trend_question(question: str) -> bool:
    return bool(
        re.search(r"营收|营业收入|利润|现金流|revenue|income|profit|cash.flow", question, re.I)
        and re.search(
            r"历年|逐年|(?:近|过去|最近)[三五35](?:个)?(?:财)?年|(?<!\d)(?:3|5|three|five)[ -]years?\b|annual trend|multi.year",
            question,
            re.I,
        )
        and not re.search(
            r"季度|TTM|为什么|为何|原因|解释|why|driver|利润率|毛利率|同比|增速|增长率|"
            r"growth|margin|净资本|净额|股价|市盈率|price|valuation|\bFY\s*20\d{2}\b|"
            r"毛利|gross|EPS|每股|成本|资产|负债|股息|cost|asset|debt|dividend",
            question,
            re.I,
        )
    )


def trend_years(question: str) -> int:
    return 3 if re.search(r"(?:三|3)(?:个)?(?:财)?年|(?<!\d)(?:3|three)[ -]years?\b", question, re.I) else 5
