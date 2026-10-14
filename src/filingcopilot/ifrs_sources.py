"""Native consolidated annual XBRL facts from immutable inline SEC originals.

This source path does not translate currency or infer ADS earnings. Separately
registered reported USD ADS EPS requires its exact share-class dimension. Only
reviewed issuer/metric profiles apply; ordinary consolidated facts stay native.
"""

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .corpus import stable_id
from .domain import DEFAULT_DOMAIN, Domain
from .models import Fact, Filing
from .periods import period_kind


def extract_ifrs_facts(html: bytes, filing: Filing, domain: Domain = DEFAULT_DOMAIN) -> list[Fact]:
    profile = domain.financial_profiles.get(filing.ticker)
    if filing.form not in ("20-F", "20-F/A") or not profile or profile.cik != filing.cik:
        return []
    return _extract_ifrs_facts(BeautifulSoup(html, "html.parser"), filing, domain)


def _extract_ifrs_facts(soup: BeautifulSoup, filing: Filing, domain: Domain) -> list[Fact]:
    """Read facts before narrative extraction removes inline resources from this tree."""
    profile = domain.financial_profiles.get(filing.ticker)
    if filing.form not in ("20-F", "20-F/A") or not profile or profile.cik != filing.cik:
        return []
    identifiers = defaultdict(list)
    for node in soup.find_all(id=True):
        identifiers[node["id"]].append(node)

    def unique(identifier):
        nodes = identifiers.get(identifier, [])
        return nodes[0] if len(nodes) == 1 else None

    candidates = defaultdict(list)
    for node in soup.find_all("ix:nonfraction"):
        concept = node.get("name", "")
        context = unique(node.get("contextref"))
        members = context.find_all("xbrldi:explicitmember") if context else []
        dimensions = {m.get("dimension"): m.get_text().strip() for m in members}
        # No unregistered segment, scenario, typed member or duplicate axis can
        # be collapsed into a consolidated or ADS total.
        if (
            context is None
            or context.find("xbrli:scenario")
            or context.find("xbrldi:typedmember")
            or len(dimensions) != len(members)
        ):
            continue
        segments = context.find_all("xbrli:segment")
        if segments and (
            len(segments) != 1
            or not members
            or segments[0].get_text(" ", strip=True)
            != " ".join(m.get_text().strip() for m in members)
        ):
            continue
        metric = next(
            (
                k
                for k, spec in profile.metrics.items()
                if concept in spec.concepts and spec.dimensions == dimensions
            ),
            None,
        )
        if metric is None:
            continue
        try:
            spec = profile.metrics[metric]
            context, unit = unique(node.get("contextref")), unique(node.get("unitref"))
            identity = context.find("xbrli:identifier") if context else None
            if (
                identity is None
                or identity.get("scheme") != "http://www.sec.gov/CIK"
                or identity.get_text().strip().zfill(10) != filing.cik
                or unit is None
                or unique(node.get("id")) is not node
                or node.get("xsi:nil") in ("true", "1")
            ):
                continue
            if spec.unit.endswith(("/shares", "/ADS")):
                numerator, denominator = (
                    unit.find("xbrli:unitnumerator"),
                    unit.find("xbrli:unitdenominator"),
                )
                if (
                    numerator is None
                    or denominator is None
                    or len(unit.find_all("xbrli:measure")) != 2
                    or numerator.get_text().strip() != "iso4217:" + spec.unit.split("/")[0]
                    or denominator.get_text().strip() != "xbrli:shares"
                ):
                    continue
            elif (
                unit.find("xbrli:divide")
                or len(unit.find_all("xbrli:measure")) != 1
                or unit.get_text().strip() != "iso4217:" + profile.currency
            ):
                continue
            start_node = context.find("xbrli:startdate")
            end_node = context.find("xbrli:enddate") or context.find("xbrli:instant")
            start = date.fromisoformat(start_node.get_text().strip()) if start_node else None
            end = date.fromisoformat(end_node.get_text().strip())
            balance = metric in ("total_assets", "cash")
            if (
                (start is None) != balance
                or end > filing.period_end
                or period_kind(start, end, filing.form) != "annual"
            ):
                continue
            fmt = node.get("format", "").split(":")[-1]
            text = node.get_text().strip().replace("\u00a0", "")
            if fmt in ("fixed-zero", "numdash"):
                value = Decimal(0)
            elif fmt in ("", "num-dot-decimal", "numdotdecimal") and re.fullmatch(
                r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text
            ):
                value = Decimal(text.replace(",", ""))
            else:
                continue
            scale = int(node.get("scale", "0"))
            decimals = node.get("decimals", "")
            if not -12 <= scale <= 12 or node.get("sign", "") not in ("", "-"):
                continue
            if decimals != "INF" and not re.fullmatch(r"-?\d{1,2}", decimals):
                continue
            precision = 100 if decimals == "INF" else int(decimals)
            if not -12 <= precision <= 12 and precision != 100:
                continue
            value *= Decimal(10) ** scale
            if node.get("sign") == "-":
                value = -value
            if not value.is_finite():
                continue
            fact = Fact(
                id=stable_id(
                    "ifrs-fact" if profile.taxonomy == "ifrs-full" else "native-us-gaap-fact",
                    filing.id, metric, start, end, spec.unit, value, node["id"]
                ),
                filing_id=filing.id,
                ticker=filing.ticker,
                metric=metric,
                label=spec.label,
                concept=concept,
                value=value,
                unit=spec.unit,
                fiscal_year=end.year,
                start=start,
                end=end,
                source_url=filing.source_url,
                locator=("inline-ifrs:id=" if profile.taxonomy == "ifrs-full" else "inline-us-gaap:id=")
                + node["id"]
                + "; decimals="
                + decimals
                + (
                    "; share_class=" + next(iter(spec.dimensions.values()))
                    if spec.dimensions
                    else ""
                ),
            )
            candidates[(metric, start, end)].append((precision, fact))
        except (ValueError, AttributeError, InvalidOperation):
            continue
    result = []
    for versions in candidates.values():
        best_precision = max(p for p, f in versions)
        best = [f for p, f in versions if p == best_precision]
        # Duplicate statements can disclose rounded figures. Prefer the most
        # precise only when every coarser XBRL interval agrees; conflict withholds
        # the metric rather than silently choosing a conveniently matching row.
        if len({(f.value, f.concept) for f in best}) != 1:
            continue
        selected = min(best, key=lambda f: f.locator)
        if all(
            f.value == selected.value
            if p == 100
            else abs(f.value - selected.value) <= Decimal(10) ** -p / 2
            for p, f in versions
        ):
            result.append(selected)
    return result
