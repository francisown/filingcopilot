"""Independent public transcript/news ingestion.

The SEC update path remains the authoritative official-notice path.  This module
handles explicitly configured RSS/Atom/HTML sources in a separate namespace and
storage table. RSS/Atom entries must provide a timezone-aware publication
timestamp. An HTML transcript without a machine-readable timestamp is marked
``first_observed`` and is never backdated. Items unavailable by ``as_of`` are
discarded before they reach research.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pydantic import Field, model_validator

from .execution import bounded_timeout, checkpoint
from .models import ExternalUpdate, StrictModel


class ExternalSource(StrictModel):
    """A deliberately explicit feed registration for one listing."""

    source_type: str = Field(pattern=r"^(transcript|news)$")
    source_name: str = Field(min_length=1, max_length=160)
    feed_url: str = Field(pattern=r"^https://")
    evidence_level: str = Field(
        pattern=r"^(issuer_transcript|issuer_news|licensed_news|public_news)$"
    )
    format: str = Field(default="rss", pattern=r"^(rss|html_index)$")
    link_pattern: str | None = None
    text_selector: str | None = None

    @model_validator(mode="after")
    def valid_source(self):
        if self.source_type == "transcript" and self.evidence_level != "issuer_transcript":
            raise ValueError("Transcript registration requires issuer_transcript")
        if self.source_type == "news" and self.evidence_level == "issuer_transcript":
            raise ValueError("News registration cannot claim transcript evidence")
        if self.format == "html_index" and (not self.link_pattern or not self.text_selector):
            raise ValueError("HTML index requires explicit link and text selectors")
        if self.link_pattern:
            re.compile(self.link_pattern)
        return self


class PublicExternalFetcher:
    """Small immutable-cache fetcher for explicitly registered HTTPS feeds."""

    def __init__(self, cache: Path, user_agent: str = "Mozilla/5.0 (compatible; FilingCopilot/1.0; +research)"):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        (cache / "raw").mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent

    def fetch(self, url: str, max_age: float | None = 3600) -> bytes:
        checkpoint()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            raise ValueError("External feeds must use a public HTTPS URL")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + ".cache")
        if path.is_file() and (max_age is None or time.time() - path.stat().st_mtime < max_age):
            return path.read_bytes()
        response = httpx.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=bounded_timeout(20),
            follow_redirects=False,
        )
        response.raise_for_status()
        content = response.content
        path.write_bytes(content)
        (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
        return content


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _text(node) -> str:
    raw = "".join(node.itertext())
    if "<" not in raw:
        return " ".join(html.unescape(raw).split())
    return " ".join(BeautifulSoup(raw, "html.parser").stripped_strings)


def _first(node, names: tuple[str, ...]):
    for child in list(node):
        if _local_name(child.tag) in names:
            return child
    return None


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = html.unescape(raw.strip())
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_feed(payload: bytes, source: ExternalSource, ticker: str, retrieved_at=None) -> list[ExternalUpdate]:
    """Parse RSS 2.0 or Atom without treating feed text as trusted instructions."""

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ValueError("External feed is not valid XML") from exc
    retrieved = retrieved_at or datetime.now(timezone.utc)
    nodes = [node for node in root.iter() if _local_name(node.tag) in ("item", "entry")]
    updates = []
    for node in nodes:
        title_node = _first(node, ("title",))
        link_node = _first(node, ("link",))
        link = (link_node.get("href") if link_node is not None else None) or (
            _text(link_node) if link_node is not None else ""
        )
        link = urljoin(source.feed_url, html.unescape(link.strip()))
        date_node = _first(node, ("pubdate", "published", "updated", "date"))
        published = _parse_datetime(_text(date_node) if date_node is not None else None)
        if published is None:
            # An undated item is not evidence: do not guess from retrieval time.
            continue
        title = _text(title_node) if title_node is not None else ""
        content_node = _first(node, ("encoded", "content", "summary", "description"))
        text = _text(content_node) if content_node is not None else title
        if not title or not text or not link.startswith("https://"):
            continue
        body = f"{title}\n{text}"[:3500]
        sha = hashlib.sha256(payload).hexdigest()
        identity = hashlib.sha256(
            "|".join((ticker.upper(), source.source_type, link, published.isoformat(), sha)).encode()
        ).hexdigest()[:20]
        updates.append(
            ExternalUpdate(
                id=f"external-{identity}",
                ticker=ticker.upper(),
                source_type=source.source_type,
                source_name=source.source_name,
                evidence_level=source.evidence_level,
                title=title[:500],
                text=body,
                source_url=link,
                source_feed_url=source.feed_url,
                source_config=source.model_dump(),
                published_at=published,
                available_at=published,
                content_sha256=sha,
                retrieved_at=retrieved,
            )
        )
    return updates


def transcript_links(payload: bytes, source: ExternalSource) -> list[str]:
    base = urlsplit(source.feed_url)
    links = {
        urljoin(source.feed_url, anchor["href"])
        for anchor in BeautifulSoup(payload, "html.parser").find_all("a", href=True)
    }
    return sorted(
        (link for link in links if urlsplit(link).netloc == base.netloc
         and re.fullmatch(source.link_pattern, urlsplit(link).path)), reverse=True
    )[:2]


def parse_transcript(payload: bytes, source: ExternalSource, ticker: str, url: str, retrieved_at=None) -> list[ExternalUpdate]:
    soup = BeautifulSoup(payload, "html.parser")
    title_node = soup.find("h1") or soup.find("title")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    nodes = soup.select(source.text_selector)
    texts = [node.get_text(" ", strip=True) for node in nodes]
    body = max(texts, key=len, default="")
    if not title or len(body) < 200 or not re.search(r"transcript|conference call|earnings call", title + " " + body, re.I):
        return []
    published_node = soup.find("meta", attrs={"property": "article:published_time"})
    published = _parse_datetime(published_node.get("content")) if published_node else None
    retrieved = retrieved_at or datetime.now(timezone.utc)
    sha = hashlib.sha256(payload).hexdigest()
    identity = hashlib.sha256("|".join((ticker.upper(), url, sha)).encode()).hexdigest()[:20]
    return [ExternalUpdate(
        id="external-" + identity, ticker=ticker.upper(), source_type="transcript",
        source_name=source.source_name, evidence_level=source.evidence_level,
        title=title[:500], text=body[:3500], source_url=url, source_feed_url=source.feed_url,
        source_config=source.model_dump(),
        published_at=published, available_at=published or retrieved,
        publication_basis="source_timestamp" if published else "first_observed",
        text_scope="transcript_excerpt", content_sha256=sha, retrieved_at=retrieved,
    )]


def acquire_external_updates(
    fetcher,
    ticker: str,
    sources: list[ExternalSource],
    as_of: date,
    days: int = 90,
    limit: int = 5,
    on_update=None,
) -> dict:
    """Fetch configured sources, filter publication time, and return separated sections."""

    if as_of > date.today() or not 1 <= days <= 365 or not 1 <= limit <= 10:
        raise ValueError("External update scope requires a past/current date and bounded window")
    start = as_of - timedelta(days=days - 1)
    sections = {"transcripts": [], "news": []}
    warnings = []
    for source in sources:
        try:
            payload = fetcher.fetch(source.feed_url)
            if source.format == "rss":
                updates = parse_feed(payload, source, ticker)
            else:
                updates = []
                for url in transcript_links(payload, source):
                    updates.extend(parse_transcript(fetcher.fetch(url), source, ticker, url))
        except (httpx.HTTPError, OSError, ValueError, ElementTree.ParseError) as exc:
            warnings.append(f"{source.source_name} 外部资料未取齐（{type(exc).__name__}），可稍后重试。")
            continue
        selected = sorted([
            u
            for u in updates
            if start <= u.available_at.date() <= as_of
        ], key=lambda u: (u.available_at, u.id), reverse=True)[:limit]
        for update in selected:
            if on_update:
                on_update(update)
            sections["transcripts" if update.source_type == "transcript" else "news"].append(
                update.model_dump(mode="json")
            )
    for key in sections:
        sections[key].sort(key=lambda item: (item["available_at"], item["id"]), reverse=True)
        sections[key] = sections[key][:limit]
    return {
        "window_start": start.isoformat(),
        "as_of": as_of.isoformat(),
        "sections": sections,
        "warnings": warnings,
        "scope": "公开电话会逐字稿与新闻为独立外部来源；按 available_at 过滤（有 published_at 时采用公开时间），不等同 SEC 官方公告或已核验财务事实。",
    }


def external_scope(as_of: date, days: int = 90) -> str:
    if as_of > date.today() or not 1 <= days <= 365:
        raise ValueError("External update scope requires a past/current date and bounded window")
    return (
        f"近 {days} 天公开电话会逐字稿与新闻（按来源公开时间或首次取得时间筛选至 {as_of.isoformat()}）；"
        "来源与 SEC 官方公告分区保存，外部文字不重建财务事实。"
    )
