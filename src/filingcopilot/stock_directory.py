"""Names resolve to listings; a listing record does not promise available quotes."""

import json
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .external_updates import ExternalSource
from .models import StrictModel


class StockListing(StrictModel):
    ticker: str = Field(pattern=r"^[A-Z0-9^][A-Z0-9.^=-]{0,14}$")
    name: str = Field(min_length=1)
    exchange: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    product: str
    industry: str | None = None
    type: Literal["EQUITY", "ETF", "INDEX"] = "EQUITY"
    filings_supported: bool = False
    annual_form: Literal["10-K", "20-F"] = "10-K"
    annual_text_only: bool = False
    annual_financials: bool = False
    update_form: Literal["8-K", "6-K"] | None = None
    filing_note: str
    source_url: str = Field(pattern=r"^https://")
    external_sources: list[ExternalSource] = Field(default_factory=list)
    # HKEXnews uses its own official title-search identity.  Keep this separate
    # from issuer RSS/transcript registrations so an HKEX announcement cannot be
    # mistaken for third-party news or an SEC filing.
    hkex_stock_code: str | None = Field(default=None, pattern=r"^\d{5}$")
    cninfo_stock_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    cninfo_column: Literal["sse", "szse", "bj"] = "sse"

    @model_validator(mode="after")
    def reporting_scope(self):
        if self.annual_form == "20-F" and not (self.annual_text_only or self.annual_financials):
            raise ValueError("20-F requires a declared annual source capability")
        if (self.annual_text_only or self.annual_financials) and not self.filings_supported:
            raise ValueError("Annual source capability requires filing acquisition")
        if self.annual_text_only and self.annual_financials:
            raise ValueError("Text-only and financial source capabilities differ")
        if self.update_form and not self.filings_supported:
            raise ValueError("Company notices require a supported filing identity")
        if self.update_form == "6-K" and self.annual_form != "20-F":
            raise ValueError("6-K requires a foreign issuer reporting scope")
        return self


class StockIdentity(StrictModel):
    aliases: list[str] = Field(min_length=1)
    listings: list[StockListing] = Field(min_length=1)


class StockDirectory:
    def __init__(self, path: Path | None = None):
        payload = json.loads(
            files("filingcopilot").joinpath("stock-directory.json").read_text("utf-8")
        )
        if path:
            payload.update(json.loads(path.read_text("utf-8")))
        self.identities = [StockIdentity.model_validate(row) for row in payload.values()]

    def listing(self, ticker: str) -> StockListing | None:
        return next(
            (
                row
                for identity in self.identities
                for row in identity.listings
                if row.ticker == ticker.upper()
            ),
            None,
        )

    def lookup(self, query: str) -> list[dict]:
        query = query.strip().casefold()
        explicit = self.listing(query)
        if explicit:
            return [explicit.model_dump()]
        for identity in self.identities:
            if query in {name.casefold() for name in identity.aliases}:
                return [row.model_dump() for row in identity.listings]
        return []


def directory_enriched_companies(companies: dict, directory: StockDirectory) -> dict:
    """Add unambiguous listing aliases to a company snapshot deterministically.

    The directory is a local identity aid, not a filing or market source.  This
    helper is shared by live research and CLI package audits so a frozen package
    receives the same aliases in both paths.
    """

    enriched = dict(companies)
    alias_targets: dict[str, set[str]] = {}
    alias_labels: dict[str, str] = {}
    for identity in directory.identities:
        for alias in identity.aliases:
            key = alias.casefold()
            alias_labels[key] = alias
            alias_targets.setdefault(key, set()).update(
                listing.ticker for listing in identity.listings
            )
    for key, targets in alias_targets.items():
        if len(targets) != 1:
            continue
        ticker = next(iter(targets))
        if ticker not in enriched:
            continue
        company = enriched[ticker]
        enriched[ticker] = {
            **company,
            "aliases": list(dict.fromkeys([*company.get("aliases", []), alias_labels[key]])),
        }
    return enriched
