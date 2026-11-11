"""Incremental SEC discovery for Monitor v1.

The existing `CompanyDiscovery` pipeline owns HTML/XBRL extraction and caching.
This module only decides what is new, fetches the official submissions index and
records enough metadata (acceptance time, 8-K items) to classify events.
"""

from __future__ import annotations

import json
from datetime import date

import httpx

from .taxonomy import SUPPORTED_FORMS, form_root, normalise_form


class MonitoringDiscovery:
    def __init__(self, service):
        self.service = service

    def _fetcher(self):
        fetcher = getattr(self.service.company_discovery, "fetcher", None)
        if fetcher is not None:
            return fetcher
        from ..ingestion import SecFetcher

        return SecFetcher(
            self.service.settings.home / "sec-cache",
            self.service.settings.sec_user_agent,
        )

    def cik(self, ticker: str) -> str:
        ticker = ticker.upper()
        company = self.service.domain.companies.get(ticker)
        if company is not None:
            return str(company.cik)
        listing = self.service.market.directory.listing(ticker)
        cik = getattr(listing, "cik", None) if listing else None
        if cik:
            return str(cik)
        raise ValueError(f"No SEC CIK is registered for {ticker}")

    def submissions(self, ticker: str, as_of: date) -> list[dict]:
        cik = self.cik(ticker)
        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        raw = self._fetcher().fetch(url, offline=False, max_age=300)
        payload = json.loads(raw)
        recent = payload.get("filings", {}).get("recent") or {}
        accessions = recent.get("accessionNumber") or []
        if not accessions:
            return []
        length = len(accessions)
        columns = {
            key: recent.get(key) or [None] * length
            for key in (
                "accessionNumber",
                "filingDate",
                "acceptanceDateTime",
                "reportDate",
                "form",
                "primaryDocument",
                "items",
                "primaryDocDescription",
            )
        }
        rows: list[dict] = []
        for index in range(length):
            form = normalise_form(columns["form"][index])
            if form not in SUPPORTED_FORMS:
                continue
            filed = str(columns["filingDate"][index] or "")
            if not filed or filed > as_of.isoformat():
                continue
            accession = str(columns["accessionNumber"][index] or "")
            if not accession:
                continue
            rows.append(
                {
                    "filing_id": f"{ticker.upper()}:{accession}",
                    "accession": accession,
                    "form": form,
                    "filed": filed,
                    "accepted": str(columns["acceptanceDateTime"][index] or filed),
                    "period_end": str(columns["reportDate"][index] or filed),
                    "primary_document": str(columns["primaryDocument"][index] or ""),
                    "items": str(columns["items"][index] or ""),
                    "description": str(columns["primaryDocDescription"][index] or ""),
                }
            )
        return rows

    def new_metadata(self, ticker: str, as_of: date, metadata: list[dict]) -> list[dict]:
        local = {item["id"] for item in self.service.corpus.catalog(as_of, ticker)}
        return [item for item in metadata if item["filing_id"] not in local]

    def prepare(self, ticker: str, as_of: date, forms: list[str]) -> dict:
        """Use the existing extraction pipeline for the requested form families."""
        roots = {form_root(form) for form in forms}
        result: dict = {"prepared": [], "warnings": []}

        def record(form: str, call) -> None:
            try:
                value = call()
                result["prepared"].append({"form": form, "result": value})
                if isinstance(value, dict):
                    if value.get("checked") is False:
                        # The underlying preparation failed or timed out. This
                        # must block watermark advancement and be retried.
                        result["warnings"].append(
                            f"{ticker} {form} 检查未完成："
                            f"{value.get('warning') or 'checked=False'}"
                        )
                    elif value.get("warning"):
                        result["warnings"].append(
                            f"{ticker} {form} 获取提示：{value['warning']}"
                        )
            except Exception as exc:  # acquisition failures must not abort a scan
                result["warnings"].append(
                    f"{ticker} {form} 获取未完成：{type(exc).__name__}"
                )

        if "10-Q" in roots:
            record(
                "10-Q",
                lambda: self.service.company_discovery.prepare_quarterly(
                    ticker, as_of
                ),
            )
        if "10-K" in roots:
            record(
                "10-K",
                lambda: self.service.company_discovery.prepare_annual_facts(
                    ticker, as_of
                ),
            )
        if "8-K" in roots:
            pending = [
                row["accession"]
                for row in self.service.store.list_filings(ticker)
                if row.get("status") in {"pending", "failed"}
                and str(row.get("form") or "").startswith("8-K")
                and row.get("accession")
            ]
            record(
                "8-K",
                lambda: self.service.company_discovery.prepare_updates(
                    ticker, as_of, accessions=pending or None
                ),
            )
        return result

    def refresh(self, ticker: str, as_of: date, forms: list[str]) -> dict:
        """Return metadata, newly observed filings and acquisition warnings."""
        result: dict = {
            "ticker": ticker.upper(),
            "metadata": [],
            "new": [],
            "prepared": [],
            "warnings": [],
        }
        user_agent = str(getattr(self.service.settings, "sec_user_agent", "") or "")
        if "@" not in user_agent:
            result["warnings"].append(
                f"{ticker} 实时发现未启用：请在 .env 设置 FC_SEC_USER_AGENT"
                "（真实联系邮箱）。本次仅使用本地已有资料。"
            )
            return result
        try:
            metadata = self.submissions(ticker, as_of)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            result["warnings"].append(
                f"{ticker} 官方申报索引未更新：{type(exc).__name__}。已保留本地资料。"
            )
            return result
        result["metadata"] = metadata
        new = self.new_metadata(ticker, as_of, metadata)
        result["new"] = new
        if not new:
            return result
        prepared = self.prepare(ticker, as_of, forms)
        result["prepared"] = prepared["prepared"]
        result["warnings"].extend(prepared["warnings"])
        return result
