"""Official HKEXnews title-search announcements.

HKEXnews is deliberately kept out of the SEC filing and issuer-news paths.  The
title search returns an official release timestamp and a document URL; each
English and Chinese document is stored as its own evidence item.  A PDF that
cannot be text-extracted is still useful as a dated, linked announcement title,
but is never promoted to a financial fact.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import Field

from .execution import bounded_timeout, checkpoint
from .localized_annual import LocalizedAnnualFact, extract_local_annual_metrics
from .models import ExternalUpdate, StrictModel

HKEX_HOSTS = {"www1.hkexnews.hk", "www.hkexnews.hk"}
HK_TZ = timezone(timedelta(hours=8))


class HKEXSource(StrictModel):
    """Registration for one Hong Kong listed equity."""

    source_type: str = Field(default="hkex", pattern=r"^hkex$")
    source_name: str = Field(default="HKEXnews", min_length=1, max_length=160)
    stock_code: str = Field(pattern=r"^\d{5}$")
    market: str = Field(default="SEHK", pattern=r"^SEHK$")
    search_host: Literal["www1.hkexnews.hk"] = "www1.hkexnews.hk"

    @property
    def search_url(self) -> str:
        return f"https://{self.search_host}/search/titlesearch.xhtml"


class HKEXFetcher:
    """Small immutable-cache client for the public HKEX title-search pages."""

    def __init__(
        self,
        cache: Path,
        user_agent: str = "Mozilla/5.0 (compatible; FilingCopilot HKEX research)",
    ):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        (cache / "raw").mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent

    def _get(self, url: str) -> httpx.Response:
        checkpoint()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HKEX_HOSTS:
            raise ValueError("HKEX source URL must use the official HKEXnews HTTPS host")
        return httpx.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=bounded_timeout(30),
            follow_redirects=True,
        )

    def fetch_document(self, url: str, max_age: float | None = 3600) -> bytes:
        """Fetch one official announcement and retain its response by content hash."""

        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in HKEX_HOSTS
            or not parsed.path.startswith("/listedco/listconews/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("HKEX announcement URL is outside the official document path")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + ".cache")
        if path.is_file() and (max_age is None or time.time() - path.stat().st_mtime < max_age):
            return path.read_bytes()
        response = self._get(url)
        response.raise_for_status()
        content = response.content
        path.write_bytes(content)
        (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
        return content

    def search(self, source: HKEXSource, language: str, start: date, end: date) -> tuple[bytes, str]:
        if language not in ("en", "zh"):
            raise ValueError("HKEX language must be en or zh")
        lang = "en" if language == "en" else "zh"
        page_url = f"{source.search_url}?lang={lang}&market={source.market}"
        # A fresh GET supplies the JavaServer Faces view state and a session-bound
        # POST action.  The result is deterministic for the requested date/code;
        # the response itself is retained in the cache for traceability.
        client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=bounded_timeout(30),
            follow_redirects=True,
        )
        try:
            initial = client.get(page_url)
            initial.raise_for_status()
            soup = BeautifulSoup(initial.text, "html.parser")
            form = soup.find("form")
            if form is None:
                raise ValueError("HKEX title search form is missing")
            fields = {
                node.get("name"): node.get("value", "")
                for node in form.select("input[name]")
                if node.get("name")
            }
            fields.update(
                {
                    "lang": "EN" if language == "en" else "ZH",
                    "market": source.market,
                    "category": "0",
                    "searchType": "0",
                    "documentType": "-1",
                    "t1code": "-2",
                    "t2Gcode": "-2",
                    "t2code": "-2",
                    "stockId": self._stock_id(client, source, language),
                    "from": start.strftime("%Y%m%d"),
                    "to": end.strftime("%Y%m%d"),
                }
            )
            action = form.get("action") or "/search/titlesearch.xhtml"
            action_url = urljoin(page_url, action)
            parsed = urlsplit(action_url)
            if parsed.hostname not in HKEX_HOSTS:
                raise ValueError("HKEX title search action left the official host")
            result = client.post(action_url, data=fields)
            result.raise_for_status()
            content = result.content
            key = hashlib.sha256(
                (action_url + "|" + language + "|" + start.isoformat() + "|" + end.isoformat()).encode()
            ).hexdigest()
            (self.cache / ("search-" + key + ".html")).write_bytes(content)
            (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
            # Keep the canonical public search URL in evidence.  The JSF action
            # can contain a transient session suffix and must not become a
            # source identity in a frozen package.
            return content, page_url
        finally:
            client.close()

    @staticmethod
    def _stock_id(client: httpx.Client, source: HKEXSource, language: str) -> str:
        endpoint = f"https://{source.search_host}/search/prefix.do"
        response = client.get(
            endpoint,
            params={
                "lang": "EN" if language == "en" else "ZH",
                "type": "A",
                "name": source.stock_code.lstrip("0") or "0",
                "market": source.market,
                "callback": "callback",
            },
        )
        response.raise_for_status()
        match = re.search(r'"stockId"\s*:\s*(\d+)\s*,\s*"code"\s*:\s*"' + re.escape(source.stock_code) + r'"', response.text)
        if not match:
            raise ValueError(f"HKEX stock code {source.stock_code} was not returned by the official directory")
        return match.group(1)


def _release_time(row) -> datetime | None:
    cell = row.select_one("td.release-time")
    raw = cell.get_text(" ", strip=True) if cell else ""
    raw = raw.replace("Release Time:", "").replace("發放時間:", "").strip()
    try:
        return datetime.strptime(raw, "%d/%m/%Y %H:%M").replace(tzinfo=HK_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def _rows(payload: bytes, language: str, source: HKEXSource, base_url: str) -> list[dict]:
    soup = BeautifulSoup(payload, "html.parser")
    rows = []
    for row in soup.select("table tbody tr"):
        code_cell = row.select_one("td.stock-short-code")
        codes = re.findall(r"(?<!\d)\d{5}(?!\d)", code_cell.get_text(" ", strip=True)) if code_cell else []
        if source.stock_code not in codes:
            continue
        published = _release_time(row)
        anchor = row.select_one("a[href]")
        headline = row.select_one(".headline")
        title = headline.get_text(" ", strip=True) if headline else ""
        url = urljoin(base_url, anchor.get("href", "")) if anchor else ""
        parsed = urlsplit(url)
        if not published or not title or parsed.hostname not in HKEX_HOSTS or not parsed.path.startswith("/listedco/listconews/"):
            continue
        rows.append(
            {
                "published_at": published,
                "title": title[:500],
                "source_url": url,
                "language": language,
                "document_title": anchor.get_text(" ", strip=True)[:500],
                "index_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return rows


def _document_text(payload: bytes, title: str, language: str) -> str:
    if payload.lstrip().lower().startswith((b"<!doctype", b"<html", b"<body")):
        body = " ".join(BeautifulSoup(payload, "html.parser").stripped_strings)
        if body:
            return (title + "\n" + body)[:3500]
    # The original PDF/HTM remains linked and hashed.  Without a PDF parser in
    # the core install, preserve the official title rather than inventing text.
    label = "英文" if language == "en" else "中文"
    return f"{title}\nHKEXnews {label}公告原件已保存；请打开原文查看完整正文。"[:3500]


def parse_hkex_announcement(
    payload: bytes,
    source: HKEXSource,
    ticker: str,
    row: dict,
    retrieved_at: datetime | None = None,
) -> ExternalUpdate:
    retrieved = retrieved_at or datetime.now(timezone.utc)
    language = row["language"]
    sha = hashlib.sha256(payload).hexdigest()
    identity = hashlib.sha256(
        "|".join((ticker.upper(), language, row["source_url"], row["published_at"].isoformat(), sha)).encode()
    ).hexdigest()[:20]
    config = {
        **source.model_dump(),
        "language": language,
        "search_url": source.search_url,
        "document_title": row.get("document_title", ""),
    }
    if row.get("index_sha256"):
        config["index_sha256"] = row["index_sha256"]
    if row.get("counterpart_url"):
        config["counterpart_url"] = row["counterpart_url"]
    return ExternalUpdate(
        id="hkex-" + identity,
        ticker=ticker.upper(),
        source_type="hkex",
        source_name=source.source_name,
        evidence_level="hkex_official",
        title=row["title"],
        text=_document_text(payload, row["title"], language),
        source_url=row["source_url"],
        source_feed_url=source.search_url,
        source_config=config,
        published_at=row["published_at"],
        available_at=row["published_at"],
        text_scope="hkex_announcement",
        language=language,
        event_date=None,
        content_sha256=sha,
        retrieved_at=retrieved,
    )


def extract_hkex_annual_metrics(
    payload: bytes,
    *,
    ticker: str,
    filing_id: str,
    source_url: str,
    fiscal_year: int | None = None,
) -> list[LocalizedAnnualFact]:
    """Extract only explicitly labelled annual cells from an HKEX document.

    This is intentionally separate from announcement text evidence: a table cell
    is promoted to a typed annual fact only when the table includes a fiscal-year
    column and an explicit currency/unit cue.  It mirrors the CNINFO adapter but
    does not assume that HKEX layouts or currencies are uniform.
    """
    return extract_local_annual_metrics(
        payload,
        ticker=ticker,
        source_type="hkex",
        filing_id=filing_id,
        source_url=source_url,
        fiscal_year=fiscal_year,
    )


def acquire_hkex_updates(
    fetcher: HKEXFetcher,
    ticker: str,
    source: HKEXSource,
    as_of: date,
    days: int = 90,
    limit: int = 5,
    on_update=None,
) -> dict:
    if as_of > date.today() or not 1 <= days <= 365 or not 1 <= limit <= 10:
        raise ValueError("HKEX公告范围要求当前或过去知悉日期，并限制窗口与条数")
    start = as_of - timedelta(days=days - 1)
    retrieved = datetime.now(timezone.utc)
    sections = {"hkex_en": [], "hkex_zh": []}
    annual_metrics: list[dict] = []
    warnings = []
    rows_by_language: dict[str, list[dict]] = {}
    for language in ("en", "zh"):
        try:
            payload, search_url = fetcher.search(source, language, start, as_of)
            rows = _rows(payload, language, source, search_url)
            for row in rows:
                row["search_url"] = search_url
            rows_by_language[language] = rows
        except (httpx.HTTPError, OSError, ValueError) as exc:
            warnings.append(f"HKEXnews {language} 公告未取齐（{type(exc).__name__}），可稍后重试。")
            rows_by_language[language] = []

    # Pairing is explicit metadata only.  English and Chinese documents remain
    # distinct evidence IDs and can be audited independently.
    for language, rows in rows_by_language.items():
        available_rows = [
            row for row in rows
            if start <= row["published_at"].astimezone(HK_TZ).date() <= as_of
        ]
        for row in sorted(available_rows, key=lambda item: item["published_at"], reverse=True)[:limit]:
            try:
                payload = fetcher.fetch_document(row["source_url"])
                update = parse_hkex_announcement(payload, source, ticker, row, retrieved)
                annual_metrics.extend(
                    fact.model_dump(mode="json")
                    for fact in extract_hkex_annual_metrics(
                        payload,
                        ticker=ticker,
                        filing_id=update.id,
                        source_url=row["source_url"],
                    )
                )
                counterpart = next(
                    (
                        other["source_url"]
                        for other_lang, other_rows in rows_by_language.items()
                        if other_lang != language
                        for other in other_rows
                        if other["published_at"] == row["published_at"]
                        and re.sub(r"_c(?=\.[A-Za-z]+$)", "", other["source_url"])
                        == re.sub(r"_c(?=\.[A-Za-z]+$)", "", row["source_url"])
                    ),
                    None,
                )
                if counterpart:
                    payload_dict = update.model_dump()
                    payload_dict["source_config"] = {**payload_dict["source_config"], "counterpart_url": counterpart}
                    update = ExternalUpdate.model_validate(payload_dict)
                if on_update:
                    on_update(update)
                sections["hkex_en" if language == "en" else "hkex_zh"].append(
                    {**update.model_dump(mode="json"), "evidence_ids": [update.id]}
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                warnings.append(f"HKEXnews {language} 公告原件未取齐（{type(exc).__name__}），可稍后重试。")
    for key in sections:
        sections[key].sort(key=lambda item: (item["available_at"], item["id"]), reverse=True)
        sections[key] = sections[key][:limit]
    return {
        "ticker": ticker.upper(),
        "window_start": start.isoformat(),
        "as_of": as_of.isoformat(),
        "sections": sections,
        "warnings": warnings,
        "annual_metrics": annual_metrics,
        "scope": f"近 {days} 天 HKEXnews 官方公告；中英文原件分开保存，按香港时间公开时间筛选；仅从含明确财年列和单位的年报 HTML 表格提取年报指标。",
    }


def hkex_scope(as_of: date, days: int = 90) -> str:
    if as_of > date.today() or not 1 <= days <= 365:
        raise ValueError("HKEX公告范围要求当前或过去知悉日期，并限制窗口")
    return f"近 {days} 天 HKEXnews 官方公告（中英文双版本、按公开时间筛选至 {as_of.isoformat()}）；年报指标仅从显式表头/单位表格提取。"
