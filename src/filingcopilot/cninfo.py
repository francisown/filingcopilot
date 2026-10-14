"""Official CNINFO (巨潮资讯) announcements for mainland A shares.

The public disclosure search is an official front-end endpoint, not the paid
data API.  This adapter freezes the returned announcement bytes and timestamp
as Chinese ``cninfo_official`` evidence.  It intentionally does not interpret
PDF tables; the task 12 table pipeline will handle annual-report metrics.
"""

from __future__ import annotations

import hashlib
import json
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

CNINFO_HOSTS = {"www.cninfo.com.cn", "static.cninfo.com.cn"}
CN_TZ = timezone(timedelta(hours=8))


class CNInfoSource(StrictModel):
    """Registration for one SSE/SZSE/BSE A-share listing."""

    source_type: str = Field(default="cninfo", pattern=r"^cninfo$")
    source_name: str = Field(default="巨潮资讯 / CNINFO", min_length=1, max_length=160)
    stock_code: str = Field(pattern=r"^\d{6}$")
    column: str = Field(default="sse", pattern=r"^(sse|szse|bj)$")
    search_host: Literal["www.cninfo.com.cn"] = "www.cninfo.com.cn"
    static_host: Literal["static.cninfo.com.cn"] = "static.cninfo.com.cn"

    @property
    def query_url(self) -> str:
        return f"https://{self.search_host}/new/hisAnnouncement/query"

    @property
    def lookup_url(self) -> str:
        return f"https://{self.search_host}/new/information/topSearch/query"


class CNInfoFetcher:
    """Immutable-cache client for CNINFO's public disclosure front end."""

    def __init__(
        self,
        cache: Path,
        user_agent: str = "Mozilla/5.0 (compatible; FilingCopilot CNINFO research)",
    ):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        (cache / "raw").mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": "https://www.cninfo.com.cn/new/disclosure",
            "Origin": "https://www.cninfo.com.cn",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    def _post_json(self, url: str, data: dict[str, str]) -> tuple[bytes, dict]:
        checkpoint()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in CNINFO_HOSTS:
            raise ValueError("CNINFO source URL must use an official HTTPS host")
        response = httpx.post(url, headers=self._headers, data=data, timeout=bounded_timeout(30))
        response.raise_for_status()
        content = response.content
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("CNINFO response is not JSON") from exc
        return content, payload

    def _org_id(self, source: CNInfoSource) -> str:
        _, payload = self._post_json(
            source.lookup_url, {"keyWord": source.stock_code, "maxNum": "10"}
        )
        rows = payload if isinstance(payload, list) else payload.get("stockList", [])
        matches = [
            row
            for row in rows
            if str(row.get("code", "")) == source.stock_code and row.get("orgId")
        ]
        if len(matches) != 1:
            raise ValueError(f"CNINFO stock code {source.stock_code} has no unique official orgId")
        return str(matches[0]["orgId"])

    def search(self, source: CNInfoSource, start: date, end: date) -> tuple[bytes, list[dict]]:
        org_id = self._org_id(source)
        query = {
            "stock": f"{source.stock_code},{org_id}",
            "tabName": "fulltext",
            "pageSize": "30",
            "pageNum": "1",
            "column": source.column,
            "category": "",
            "seDate": f"{start.isoformat()}~{end.isoformat()}",
            "isHLtitle": "true",
        }
        content, payload = self._post_json(source.query_url, query)
        announcements = payload.get("announcements") if isinstance(payload, dict) else None
        if announcements is None:
            announcements = []
        if not isinstance(announcements, list):
            raise ValueError("CNINFO announcement response has an invalid list")
        key = hashlib.sha256(
            (source.query_url + "|" + json.dumps(query, sort_keys=True, ensure_ascii=False)).encode()
        ).hexdigest()
        (self.cache / ("search-" + key + ".json")).write_bytes(content)
        (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
        return content, [row for row in announcements if isinstance(row, dict)]

    def fetch_document(self, source: CNInfoSource, adjunct_url: str, max_age: float | None = 3600) -> bytes:
        if not isinstance(adjunct_url, str) or not adjunct_url:
            raise ValueError("CNINFO announcement has no document URL")
        url = urljoin(f"https://{source.static_host}/", adjunct_url.lstrip("/"))
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != source.static_host
            or not parsed.path.startswith("/finalpage/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("CNINFO document URL is outside the official finalpage path")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + ".cache")
        if path.is_file() and (max_age is None or time.time() - path.stat().st_mtime < max_age):
            return path.read_bytes()
        checkpoint()
        response = httpx.get(url, headers={"User-Agent": self.user_agent}, timeout=bounded_timeout(30))
        response.raise_for_status()
        content = response.content
        path.write_bytes(content)
        (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
        return content


def _published_at(raw) -> datetime | None:
    try:
        # CNINFO's epoch values are milliseconds for the displayed Chinese date.
        return datetime.fromtimestamp(float(raw) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _rows(payload: list[dict], source: CNInfoSource) -> list[dict]:
    rows = []
    for item in payload:
        if str(item.get("secCode", "")) != source.stock_code:
            continue
        published = _published_at(item.get("announcementTime"))
        title = str(item.get("announcementTitle") or item.get("shortTitle") or "").strip()
        adjunct = item.get("adjunctUrl")
        if not published or not title or not isinstance(adjunct, str):
            continue
        url = urljoin(f"https://{source.static_host}/", adjunct.lstrip("/"))
        parsed = urlsplit(url)
        if parsed.hostname != source.static_host or not parsed.path.startswith("/finalpage/"):
            continue
        rows.append(
            {
                "announcement_id": str(item.get("announcementId") or ""),
                "published_at": published,
                "title": title[:500],
                "source_url": url,
                "adjunct_url": adjunct,
                "language": "zh",
            }
        )
    return rows


def _document_text(payload: bytes, title: str) -> str:
    if payload.lstrip().lower().startswith((b"<!doctype", b"<html", b"<body")):
        body = " ".join(BeautifulSoup(payload, "html.parser").stripped_strings)
        if body:
            return (title + "\n" + body)[:3500]
    return f"{title}\n巨潮资讯中文公告原件已保存；请打开原文查看完整正文。"[:3500]


def parse_cninfo_announcement(
    payload: bytes,
    source: CNInfoSource,
    ticker: str,
    row: dict,
    retrieved_at: datetime | None = None,
) -> ExternalUpdate:
    retrieved = retrieved_at or datetime.now(timezone.utc)
    sha = hashlib.sha256(payload).hexdigest()
    identity = hashlib.sha256(
        "|".join((ticker.upper(), row["source_url"], row["published_at"].isoformat(), sha)).encode()
    ).hexdigest()[:20]
    config = {
        **source.model_dump(),
        "language": "zh",
        "publication_timezone": "Asia/Shanghai",
        "query_url": source.query_url,
        "announcement_id": row.get("announcement_id", ""),
        "adjunct_url": row.get("adjunct_url", ""),
    }
    if row.get("index_sha256"):
        config["index_sha256"] = row["index_sha256"]
    return ExternalUpdate(
        id="cninfo-" + identity,
        ticker=ticker.upper(),
        source_type="cninfo",
        source_name=source.source_name,
        evidence_level="cninfo_official",
        title=row["title"],
        text=_document_text(payload, row["title"]),
        source_url=row["source_url"],
        source_feed_url=source.query_url,
        source_config=config,
        published_at=row["published_at"],
        available_at=row["published_at"],
        text_scope="cninfo_announcement",
        language="zh",
        content_sha256=sha,
        retrieved_at=retrieved,
    )


def extract_cninfo_annual_metrics(
    payload: bytes,
    *,
    ticker: str,
    filing_id: str,
    source_url: str,
    fiscal_year: int | None = None,
) -> list[LocalizedAnnualFact]:
    """Extract conservative, table-bound annual metrics from a CNINFO HTML filing."""
    return extract_local_annual_metrics(
        payload,
        ticker=ticker,
        source_type="cninfo",
        filing_id=filing_id,
        source_url=source_url,
        fiscal_year=fiscal_year,
    )


def acquire_cninfo_updates(
    fetcher: CNInfoFetcher,
    ticker: str,
    source: CNInfoSource,
    as_of: date,
    days: int = 90,
    limit: int = 5,
    on_update=None,
) -> dict:
    if as_of > date.today() or not 1 <= days <= 365 or not 1 <= limit <= 10:
        raise ValueError("巨潮公告范围要求当前或过去知悉日期，并限制窗口与条数")
    start = as_of - timedelta(days=days - 1)
    warnings = []
    rows = []
    try:
        raw_index, payload = fetcher.search(source, start, as_of)
        rows = _rows(payload, source)
        for row in rows:
            row["index_sha256"] = hashlib.sha256(raw_index).hexdigest()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        warnings.append(f"巨潮资讯公告索引未取齐（{type(exc).__name__}），可稍后重试。")
    updates = []
    annual_metrics: list[dict] = []
    for row in sorted(rows, key=lambda item: (item["published_at"], item["source_url"]), reverse=True)[:limit]:
        local_day = row["published_at"].astimezone(CN_TZ).date()
        if not (start <= local_day <= as_of):
            continue
        try:
            payload = fetcher.fetch_document(source, row["adjunct_url"])
            update = parse_cninfo_announcement(
                payload,
                source,
                ticker,
                row,
            )
            annual_metrics.extend(
                fact.model_dump(mode="json")
                for fact in extract_cninfo_annual_metrics(
                    payload,
                    ticker=ticker,
                    filing_id=update.id,
                    source_url=row["source_url"],
                )
            )
            if on_update:
                on_update(update)
            updates.append({**update.model_dump(mode="json"), "evidence_ids": [update.id]})
        except (httpx.HTTPError, OSError, ValueError) as exc:
            warnings.append(f"巨潮资讯公告原件未取齐（{type(exc).__name__}），可稍后重试。")
    return {
        "ticker": ticker.upper(),
        "window_start": start.isoformat(),
        "as_of": as_of.isoformat(),
        "sections": {"cninfo": updates},
        "warnings": warnings,
        "annual_metrics": annual_metrics,
        "scope": f"近 {days} 天巨潮资讯官方中文公告；按披露时间筛选；仅从含明确财年列和单位的年报 HTML 表格提取年报指标。",
    }


def cninfo_scope(as_of: date, days: int = 90) -> str:
    if as_of > date.today() or not 1 <= days <= 365:
        raise ValueError("巨潮公告范围要求当前或过去知悉日期，并限制窗口")
    return f"近 {days} 天巨潮资讯官方中文公告（按披露时间筛选至 {as_of.isoformat()}）；年报指标仅从显式表头/单位表格提取。"
