"""General SEC statement rows and XBRL dimensions.

The registered ``Fact`` model intentionally stays small and conservative.  This
module provides the wider statement surface used for browsing a filing: every
numeric us-gaap row is retained with its original concept/label, while inline
XBRL contexts add segment and geographic dimensions when they are present.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .corpus import stable_id
from .models import Filing, StatementFact
from .periods import period_kind

_INCOME = re.compile(
    r"revenue|sales|income|profit|loss|expense|cost|margin|earnings|tax|interest|ebit|"
    r"gain|benefit|compensation|researchanddevelopment|depreciationandamortization",
    re.I,
)
_BALANCE = re.compile(
    r"assets?|liabilit|equity|cash|receivable|inventory|inventor|debt|payable|"
    r"capitalstock|goodwill|intangible|lease|deferred|securities|investment|"
    r"propertyplantandequipment|retainedearnings|sharesoutstanding",
    re.I,
)
_CASHFLOW = re.compile(
    r"cashflow|cashprovided|cashused|payments?to|proceedsfrom|acquire|purchase|"
    r"depreciation|amortization|stockbasedcompensation|dividend|repurchase|"
    r"operatingactivities|investingactivities|financingactivities",
    re.I,
)


def classify_statement(concept: str, label: str = "", start: date | None = None) -> str:
    """Classify conservatively; unknown rows remain browseable as ``other``."""

    text = f"{concept} {label}"
    if _CASHFLOW.search(text):
        return "cashflow"
    if _BALANCE.search(text) and start is None:
        return "balance"
    if _INCOME.search(text):
        return "income"
    if _BALANCE.search(text):
        return "balance"
    return "other"


def _valid_period(filing: Filing, start: date | None, end: date) -> bool:
    if end > filing.period_end:
        return False
    if filing.form in ("10-Q", "10-Q/A"):
        if start is None:
            return end == filing.period_end or 357 <= (filing.period_end - end).days <= 373
        return period_kind(start, end, filing.form) is not None or 357 <= (filing.period_end - end).days <= 373
    if filing.form in ("10-K", "10-K/A", "20-F", "20-F/A"):
        return start is None or period_kind(start, end, filing.form) == "annual"
    return False


def _numeric(value) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def extract_statement_facts(
    companyfacts: dict, filing: Filing, source_sha256: str | None = None
) -> list[StatementFact]:
    """Extract all numeric us-gaap rows for the selected accession.

    This deliberately does not require a domain registration.  A row with an
    unregistered concept is retained under its canonical concept key and can be
    inspected without being eligible for an automatic calculation.
    """

    result: dict[str, StatementFact] = {}
    for taxonomy, concepts in (companyfacts.get("facts", {}) or {}).items():
        if not isinstance(concepts, dict):
            continue
        for concept, definition in concepts.items():
            label = str(definition.get("label") or concept)
            units = definition.get("units", {}) or {}
            for unit, rows in units.items():
                if not isinstance(rows, list):
                    continue
                for idx, row in enumerate(rows):
                    if row.get("accn") != filing.accession or row.get("form") != filing.form:
                        continue
                    try:
                        end = date.fromisoformat(row["end"])
                        start = date.fromisoformat(row["start"]) if row.get("start") else None
                    except (KeyError, ValueError):
                        continue
                    if not _valid_period(filing, start, end):
                        continue
                    number = _numeric(row.get("val"))
                    if number is None:
                        continue
                    canonical = concept if ":" in concept else f"{taxonomy}:{concept}"
                    locator = f"facts.{taxonomy}.{concept}.units.{unit}[{idx}]; accession={filing.accession}"
                    if source_sha256:
                        locator += f"; source_sha256={source_sha256}"
                    item = StatementFact(
                        id=stable_id("statement", filing.id, canonical, unit, start, end, number, ""),
                        filing_id=filing.id,
                        ticker=filing.ticker,
                        statement=classify_statement(canonical, label, start),
                        metric=canonical,
                        label=label,
                        concept=canonical,
                        value=number,
                        unit=unit,
                        fiscal_year=end.year,
                        start=start,
                        end=end,
                        source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{filing.cik}.json",
                        locator=locator,
                    )
                    result[item.id] = item
    return list(result.values())


def _tag(soup, names: list[str]):
    for name in names:
        node = soup.find(name)
        if node is not None:
            return node
    return None


def _parse_inline_number(node) -> Decimal | None:
    text = node.get_text("", strip=True).replace("\u00a0", "").strip()
    fmt = node.get("format", "").split(":")[-1].lower()
    if fmt in ("fixed-zero", "numdash") or text in ("", "-", "—", "–"):
        return Decimal(0)
    negative = node.get("sign") == "-"
    text = text.replace(",", "")
    if not re.fullmatch(r"(?:\d+|\d+\.\d+)", text):
        return None
    try:
        value = Decimal(text) * (Decimal(10) ** int(node.get("scale", "0")))
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return -value if negative else value


def extract_inline_statement_facts(
    html: bytes, filing: Filing, *, dimensions_only: bool = True
) -> list[StatementFact]:
    """Read inline-XBRL contexts and preserve explicit segment/geography members.

    Most consolidated rows arrive through CompanyFacts.  The inline pass is kept
    separate so dimensional rows cannot be mistaken for consolidated totals.
    """

    soup = BeautifulSoup(html, "html.parser")
    contexts = {}
    for node in soup.find_all(re.compile(r"(?:^|:)context$", re.I)):
        key = node.get("id")
        if not key:
            continue
        start_node = _tag(node, ["xbrli:startdate", "startdate"])
        end_node = _tag(node, ["xbrli:enddate", "enddate"])
        instant_node = _tag(node, ["xbrli:instant", "instant"])
        try:
            end = date.fromisoformat((end_node or instant_node).get_text(strip=True))
            start = date.fromisoformat(start_node.get_text(strip=True)) if start_node else None
        except (AttributeError, ValueError):
            continue
        if not _valid_period(filing, start, end):
            continue
        dims: dict[str, str] = {}
        labels: dict[str, str] = {}
        for member in node.find_all(re.compile(r"(?:^|:)explicitmember$", re.I)):
            dimension = member.get("dimension") or ""
            value = member.get_text(strip=True)
            if dimension and value:
                dims[dimension] = value
                labels[dimension] = value.split(":")[-1]
        contexts[key] = (start, end, dims, labels)
    units = {}
    for node in soup.find_all(re.compile(r"(?:^|:)unit$", re.I)):
        key = node.get("id")
        measure = _tag(node, ["xbrli:measure", "measure"])
        if key and measure:
            units[key] = measure.get_text(strip=True)
    result = {}
    for node in soup.find_all(re.compile(r"(?:^|:)nonfraction$", re.I)):
        context = contexts.get(node.get("contextref"))
        if not context:
            continue
        start, end, dims, labels = context
        if dimensions_only and not dims:
            continue
        number = _parse_inline_number(node)
        unit = units.get(node.get("unitref"), node.get("unitref") or "unknown")
        concept = node.get("name", "")
        if number is None or not concept:
            continue
        row = node.find_parent("tr")
        label = (row.get_text(" ", strip=True)[:200] if row else concept) or concept
        item = StatementFact(
            id=stable_id("statement", filing.id, concept, unit, start, end, number, sorted(dims.items())),
            filing_id=filing.id,
            ticker=filing.ticker,
            statement=classify_statement(concept, label, start),
            metric=concept,
            label=label,
            concept=concept,
            value=number,
            unit=unit,
            fiscal_year=end.year,
            start=start,
            end=end,
            source_url=filing.source_url,
            locator="inline-xbrl:id=" + (node.get("id") or "") + "; context=" + (node.get("contextref") or ""),
            dimensions=dims,
            dimension_labels=labels,
        )
        result[item.id] = item
    return list(result.values())


def statement_rows_for_filing(rows: list[StatementFact], statement: str | None = None) -> list[StatementFact]:
    if statement not in (None, "income", "balance", "cashflow", "other"):
        raise ValueError("Unknown statement kind")
    return sorted(
        [row for row in rows if statement is None or row.statement == statement],
        key=lambda row: (row.end, row.start or date.min, row.metric, sorted(row.dimensions.items())),
    )
