"""Cash facts from consolidated inline XBRL, bound to immutable SEC HTML bytes."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .corpus import stable_id
from .domain import DEFAULT_DOMAIN, Domain
from .models import Fact, Filing
from .periods import period_kind


def extract_cash_facts(html: bytes, filing: Filing, domain: Domain = DEFAULT_DOMAIN) -> list[Fact]:
    soup = BeautifulSoup(html, "html.parser")
    identifiers = {}
    for node in soup.find_all(id=True):
        identifiers.setdefault(node["id"], []).append(node)

    def unique(identifier):
        nodes = identifiers.get(identifier, [])
        return nodes[0] if len(nodes) == 1 else None

    result = []
    for node in soup.find_all("ix:nonfraction"):
        concept = node.get("name", "")
        metric = next(
            (
                key
                for key in ("cash_capex", "ppe_sale_incentive_proceeds")
                if key in domain.metrics
                and concept
                in [c if ":" in c else "us-gaap:" + c for c in domain.metrics[key].concepts]
            ),
            None,
        )
        # ProductiveAssets includes intangibles in the taxonomy. Only the reviewed
        # Amazon PP&E cash-flow row is mapped to PP&E; never a global concept alias.
        if concept == "us-gaap:PaymentsToAcquireProductiveAssets":
            row = node.find_parent("tr")
            if (
                filing.ticker != "AMZN"
                or filing.cik != "0001018724"
                or row is None
                or not re.search(r"purchases of property and equipment", row.get_text(" "), re.I)
            ):
                continue
            metric = "cash_capex" if "cash_capex" in domain.metrics else None
        if not metric or (concept.startswith("amzn:") and filing.cik != "0001018724"):
            continue
        try:
            context = unique(node.get("contextref"))
            unit = unique(node.get("unitref"))
            identity = context.find("xbrli:identifier") if context else None
            measures = unit.find_all("xbrli:measure") if unit else []
            if (
                identity is None
                or identity.get("scheme") != "http://www.sec.gov/CIK"
                or identity.get_text().strip().zfill(10) != filing.cik
                or context.find(["xbrli:segment", "xbrli:scenario"])
                or len(measures) != 1
                or measures[0].get_text().strip() != "iso4217:USD"
                or unit.find("xbrli:divide")
                or unique(node.get("id")) is not node
                or node.get("xsi:nil") in ("true", "1")
            ):
                continue
            start = date.fromisoformat(context.find("xbrli:startdate").get_text().strip())
            end = date.fromisoformat(context.find("xbrli:enddate").get_text().strip())
            if (
                period_kind(start, end, filing.form) is None
                or end > filing.period_end
                or filing.form.startswith("10-Q")
                and end != filing.period_end
                and not 357 <= (filing.period_end - end).days <= 373
            ):
                continue
            format_name = node.get("format", "").split(":")[-1]
            text = node.get_text().strip().replace("\u00a0", "")
            if format_name in ("fixed-zero", "numdash"):
                value = Decimal(0)
            elif format_name in ("", "num-dot-decimal", "numdotdecimal") and re.fullmatch(
                r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text
            ):
                value = Decimal(text.replace(",", ""))
            else:
                continue
            scale = int(node.get("scale", "0"))
            if not -12 <= scale <= 12 or node.get("sign", "") not in ("", "-"):
                continue
            value *= Decimal(10) ** scale
            if node.get("sign") == "-":
                value = -value
            if value < 0 or not value.is_finite():
                continue
            spec = domain.metrics[metric]
            if spec.unit != "USD":
                continue
            result.append(
                Fact(
                    id=stable_id("fact", filing.id, metric, start, end, value),
                    filing_id=filing.id,
                    ticker=filing.ticker,
                    metric=metric,
                    label=spec.label,
                    concept=concept,
                    value=value,
                    unit="USD",
                    fiscal_year=end.year,
                    start=start,
                    end=end,
                    source_url=filing.source_url,
                    locator="inline-xbrl:id=" + node["id"],
                )
            )
        except (ValueError, AttributeError, InvalidOperation):
            continue
    return list({fact.id: fact for fact in result}.values())
