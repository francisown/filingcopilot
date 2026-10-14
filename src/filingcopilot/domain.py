"""Validated registry shared by acquisition, retrieval, tools and verification."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Company(Entry):
    cik: str = Field(pattern=r"^\d{10}$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class Metric(Entry):
    label: str = Field(min_length=1)
    concepts: list[str] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    unit: str = "USD"
    dimensions: dict[str, str] = Field(default_factory=dict)


class Ratio(Entry):
    label: str
    numerator: str
    denominator: str
    aliases: list[str]


class FinancialProfile(Entry):
    cik: str = Field(pattern=r"^\d{10}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    taxonomy: Literal["ifrs-full", "us-gaap"] = "ifrs-full"
    metrics: dict[str, Metric]
    ratio_labels: dict[str, str] = Field(default_factory=dict)
    industry: str | None = None


class IndustryProfile(Entry):
    """Industry-specific KPI registry kept separate from the universal three tables."""

    label: str = Field(min_length=1)
    metrics: dict[str, Metric] = Field(default_factory=dict)


def mentions(text: str, term: str) -> bool:
    if not term:
        return False
    pattern = re.escape(term)
    if term.isascii():
        pattern = r"(?<![a-z0-9_])" + pattern + r"(?![a-z0-9_])"
    return re.search(pattern, text, re.I) is not None


class Domain(Entry):
    companies: dict[str, Company]
    metrics: dict[str, Metric]
    ratios: dict[str, Ratio]
    query_expansions: dict[str, str]
    currencies: dict[str, str]
    financial_profiles: dict[str, FinancialProfile] = Field(default_factory=dict)
    industry_profiles: dict[str, IndustryProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_registry(self):
        if any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", key) for key in self.companies):
            raise ValueError("Company keys must be uppercase ticker symbols")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in self.metrics):
            raise ValueError("Metric keys must be lowercase identifiers")
        for ratio in self.ratios.values():
            if ratio.numerator not in self.metrics or ratio.denominator not in self.metrics:
                raise ValueError("Ratio references an unknown metric")
        for ticker, profile in self.financial_profiles.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
                raise ValueError("Financial profile requires a canonical ticker")
            if ticker in self.companies and self.companies[ticker].cik != profile.cik:
                raise ValueError("Financial profile identity differs from company")
            if not profile.metrics or set(profile.metrics) - set(self.metrics):
                raise ValueError("Financial profile references unknown metrics")
            if set(profile.ratio_labels) - set(self.ratios):
                raise ValueError("Financial profile references unknown ratios")
            if profile.currency not in self.currencies or any(
                any(not c.startswith(profile.taxonomy + ":") for c in m.concepts)
                or not (
                    not m.dimensions
                    and m.unit in (profile.currency, profile.currency + "/shares")
                    or key == "diluted_eps"
                    and m.unit == profile.currency + "/shares"
                    and m.concepts == ["ifrs-full:DilutedEarningsLossPerShare"]
                    and set(m.dimensions) == {"ifrs-full:ClassesOfShareCapitalAxis"}
                    and re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9-]*:OrdinarySharesMember",
                        next(iter(m.dimensions.values()), ""),
                    )
                    or key == "ads_diluted_eps"
                    and m.unit == "USD/ADS"
                    and m.concepts == ["ifrs-full:DilutedEarningsLossPerShare"]
                    and set(m.dimensions) == {"ifrs-full:ClassesOfShareCapitalAxis"}
                    and re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9-]*:AmericanDepositaryShares?Member",
                        next(iter(m.dimensions.values()), ""),
                    )
                )
                for key, m in profile.metrics.items()
            ):
                raise ValueError("Financial profile requires explicit native IFRS/US-GAAP units/concepts")
            if profile.industry and profile.industry not in self.industry_profiles:
                raise ValueError("Financial profile references an unknown industry profile")
        return self

    def metric_for(self, ticker: str, metric: str) -> Metric:
        profile = self.financial_profiles.get(ticker.upper())
        return (
            profile.metrics[metric]
            if profile and metric in profile.metrics
            else self.metrics[metric]
        )

    def annual_forms(self, ticker: str) -> tuple[str, str]:
        return (
            ("20-F", "20-F/A") if ticker.upper() in self.financial_profiles else ("10-K", "10-K/A")
        )

    def ratio_label(self, ticker: str, key: str) -> str:
        profile = self.financial_profiles.get(ticker.upper())
        return (
            profile.ratio_labels.get(key, self.ratios[key].label)
            if profile
            else self.ratios[key].label
        )

    def industry_profile(self, ticker: str) -> IndustryProfile | None:
        profile = self.financial_profiles.get(ticker.upper())
        return self.industry_profiles.get(profile.industry) if profile and profile.industry else None

    def industry_metrics(self, ticker: str) -> dict[str, Metric]:
        profile = self.industry_profile(ticker)
        return dict(profile.metrics) if profile else {}

    def company_scope(self, question: str) -> set[str]:
        return {
            ticker
            for ticker, company in self.companies.items()
            if any(mentions(question, term) for term in [ticker, company.name, *company.aliases])
        }

    def requests(self, question: str) -> tuple[set[str], set[str]]:
        # Longest phrases win: cost of revenue is not revenue, 净利率 is not 净利.
        terms = [
            (alias, "metric", key)
            for key, metric in self.metrics.items()
            for alias in [key.replace("_", " "), metric.label, *metric.aliases]
        ]
        # Query-only wording variants must not change the configuration frozen
        # in saved packages. Consume "cash" with the capex phrase, so it is not
        # mistaken for a separate point-in-time cash-balance request.
        for alias, kind, key in list(terms):
            if key == "cash_capex" and alias.casefold() == "capital expenditures":
                terms += [(phrase, kind, key) for phrase in (
                    "capital expenditure", "cash capital expenditures", "cash capital expenditure",
                )]
            elif key == "cash_capex" and alias.casefold() == "capex":
                terms.append(("cash capex", kind, key))
        terms += [
            (alias, "ratio", key)
            for key, ratio in self.ratios.items()
            for alias in [ratio.label, *ratio.aliases]
        ]
        metrics, ratios, masked = (
            set(),
            set(),
            re.sub(
                r"自由现金流|(?<![A-Za-z0-9_])FCF(?![A-Za-z0-9_])|free cash flow",
                " ",
                question,
                flags=re.I,
            ),
        )
        for term, kind, key in sorted(terms, key=lambda row: len(row[0]), reverse=True):
            if mentions(masked, term):
                (metrics if kind == "metric" else ratios).add(key)
                pattern = re.escape(term)
                if term.isascii():
                    pattern = r"(?<![a-z0-9_])" + pattern + r"(?![a-z0-9_])"
                masked = re.sub(pattern, " " * len(term), masked, flags=re.I)
        return metrics, ratios

    def allowed_metrics(self, question: str) -> set[str]:
        metrics, ratios = self.requests(question)
        return metrics | {
            metric
            for key in ratios
            for metric in (self.ratios[key].numerator, self.ratios[key].denominator)
        }


def load_domain(path: Path | None = None, companies: dict | None = None) -> Domain:
    payload = json.loads(files("filingcopilot").joinpath("domain-defaults.json").read_text("utf-8"))
    if companies:
        payload["companies"].update(companies)
    if path is not None:
        overrides = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict) or set(overrides) - set(payload):
            raise ValueError("Unknown domain configuration sections")
        for section, entries in overrides.items():
            if not isinstance(entries, dict):
                raise ValueError(f"{section} must be a JSON object")
            for key, value in entries.items():
                existing = payload[section].get(key)
                payload[section][key] = (
                    {**existing, **value}
                    if isinstance(existing, dict) and isinstance(value, dict)
                    else value
                )
    return Domain.model_validate(payload)


DEFAULT_DOMAIN = load_domain()
