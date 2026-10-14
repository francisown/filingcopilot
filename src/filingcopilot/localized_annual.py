"""Conservative HTML-table parser for HKEX/CNINFO annual-report metrics.

The parser is intentionally source-agnostic and only emits a typed value when
the table supplies an explicit fiscal-year column and a unit/currency cue. It
does not treat a filing date or a neighboring number as a reporting period.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from pydantic import Field

from .models import StrictModel


class LocalizedAnnualFact(StrictModel):
    id: str
    ticker: str
    source_type: str = Field(pattern=r"^(hkex|cninfo)$")
    source_url: str
    filing_id: str
    statement: str
    metric: str
    label: str
    value: Decimal
    unit: str
    currency: str
    fiscal_year: int
    language: str
    locator: str


METRIC_LABELS = {
    "revenue": re.compile(r"收入|营收|营业额|revenue|turnover|sales", re.I),
    "operating_income": re.compile(r"营业利润|经营溢利|operating\s+(?:income|profit)", re.I),
    "net_income": re.compile(r"净利润|纯利|profit\s+for\s+the\s+year|net\s+income", re.I),
    "operating_cash_flow": re.compile(r"经营活动.*现金|经营现金流|operating.*cash flow", re.I),
    "cash_capex": re.compile(r"资本开支|购置.*物业|capital expenditure|purchase.*property", re.I),
}
YEAR = re.compile(r"(?:FY|财年|年度|年)?\s*(20\d{2})", re.I)
NUMBER = re.compile(r"^\(?\s*[-+]?\d[\d,]*(?:\.\d+)?\s*\)?$")


def _number(value: str) -> Decimal | None:
    value = value.replace(",", "").replace(" ", "").replace("，", "")
    if not NUMBER.fullmatch(value):
        return None
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return -number if negative else number


def extract_local_annual_metrics(
    html: bytes,
    *,
    ticker: str,
    source_type: str,
    filing_id: str,
    source_url: str,
    fiscal_year: int | None = None,
) -> list[LocalizedAnnualFact]:
    if source_type not in ("hkex", "cninfo"):
        raise ValueError("localized annual source must be hkex or cninfo")
    soup = BeautifulSoup(html, "html.parser")
    result: list[LocalizedAnnualFact] = []
    for table_index, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip() for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
        if len(rows) < 2:
            continue
        headers = rows[0]
        header_years = [int(m.group(1)) for cell in headers if (m := YEAR.search(cell))]
        current_year = fiscal_year if fiscal_year is not None else (max(header_years) if header_years else None)
        if current_year is None:
            continue
        value_columns = [i for i, cell in enumerate(headers) if str(current_year) in cell]
        if len(value_columns) != 1:
            continue
        value_column = value_columns[0]
        caption = table.find("caption")
        unit_text = re.sub(r"\s+", " ", caption.get_text(" ", strip=True)).strip() if caption else ""
        unit_match = re.search(r"(HKD|港元|CNY|人民币|RMB|元|TWD|USD|美元|百万元|千元|million|thousand)", unit_text, re.I)
        if not unit_match:
            continue
        unit = unit_match.group(1)
        currency = (
            "HKD" if re.search(r"HKD|港元", unit, re.I) else
            "CNY" if re.search(r"CNY|RMB|人民币|元", unit, re.I) else
            "TWD" if re.search(r"TWD", unit, re.I) else "USD"
        )
        language = "zh" if re.search(r"[一-鿿]", " ".join(headers)) else "en"
        for row_index, row in enumerate(rows[1:], start=1):
            if not row or value_column >= len(row):
                continue
            label = row[0]
            metric = next((key for key, pattern in METRIC_LABELS.items() if pattern.search(label)), None)
            value = _number(row[value_column])
            if metric is None or value is None:
                continue
            identity = hashlib.sha256(
                "|".join((ticker.upper(), source_type, filing_id, metric, str(current_year), str(value), str(table_index), str(row_index))).encode()
            ).hexdigest()[:20]
            result.append(
                LocalizedAnnualFact(
                    id=f"localized-{identity}",
                    ticker=ticker.upper(),
                    source_type=source_type,
                    source_url=source_url,
                    filing_id=filing_id,
                    statement="cashflow" if "cash" in metric or "capex" in metric else "income",
                    metric=metric,
                    label=label,
                    value=value,
                    unit=unit,
                    currency=currency,
                    fiscal_year=current_year,
                    language=language,
                    locator=f"table {table_index + 1} row {row_index}; explicit FY{current_year} column; unit {unit}",
                )
            )
    return result
