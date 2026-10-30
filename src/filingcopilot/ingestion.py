from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .cash_sources import extract_cash_facts
from .corpus import stable_id
from .domain import DEFAULT_DOMAIN, Domain
from .execution import (
    RefreshBudgetExceeded,
    bounded_timeout,
    checkpoint,
    refresh_remaining,
)
from .execution import (
    progress as report_progress,
)
from .execution import wait as execution_wait
from .ifrs_sources import _extract_ifrs_facts
from .models import Fact, Filing, Passage, StatementFact
from .periods import period_kind
from .statements import extract_inline_statement_facts, extract_statement_facts

# Compatibility exports for snapshot scripts; runtime paths use the shared registry.
COMPANIES = {key: (c.cik, c.name) for key, c in DEFAULT_DOMAIN.companies.items()}
METRICS = {key: (m.label, m.concepts) for key, m in DEFAULT_DOMAIN.metrics.items()}


class SecRefreshDeferred(httpx.RequestError):
    """A recent failed URL is deferred, never counted as a fresh source check."""

    def __init__(self, error_type, status, retry_seconds):
        super().__init__("Recent SEC refresh failed; retry after the short cooldown")
        self.error_type = error_type
        self.status = status
        self.retry_seconds = retry_seconds


class SecFetcher:
    """Allowlisted SEC acquisition, immutable response cache, conservative request pacing."""

    _network_lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, cache: Path, user_agent: str):
        self.cache = cache
        self.user_agent = user_agent
        cache.mkdir(parents=True, exist_ok=True)
        self.last_request = 0.0
        self._refresh_failures = {}
        self._failure_lock = threading.Lock()

    def fetch(
        self,
        url: str,
        offline: bool = False,
        max_age: float | None = None,
        *,
        force_refresh: bool = False,
    ) -> bytes:
        try:
            return self._fetch(url, offline, max_age, force_refresh=force_refresh)
        except (httpx.RequestError, httpx.HTTPStatusError, RefreshBudgetExceeded) as exc:
            if refresh_remaining() is not None and not isinstance(exc, SecRefreshDeferred):
                cause = exc.__context__ if isinstance(exc.__context__, httpx.HTTPError) else exc
                status = (
                    cause.response.status_code if isinstance(cause, httpx.HTTPStatusError) else None
                )
                with self._failure_lock:
                    self._refresh_failures[url] = (
                        time.monotonic() + 60,
                        type(cause).__name__,
                        status,
                    )
            raise

    def _fetch(
        self,
        url: str,
        offline: bool,
        max_age: float | None,
        *,
        force_refresh: bool = False,
    ) -> bytes:
        checkpoint()
        parsed = urlsplit(url)
        allowed = (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.port
            and (
                parsed.netloc == "data.sec.gov"
                or parsed.netloc == "www.sec.gov"
                and (
                    parsed.path.startswith("/Archives/")
                    or parsed.path
                    in ("/files/company_tickers.json", "/files/company_tickers_exchange.json")
                )
            )
        )
        if not allowed:
            raise ValueError("Only official SEC data and filing URLs are supported")
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")
        if not force_refresh and path.exists() and (
            offline or max_age is None or time.time() - path.stat().st_mtime < max_age
        ):
            return path.read_bytes()
        if offline:
            raise ValueError("Requested SEC document is not in the local cache")
        if not self.user_agent or "@" not in self.user_agent:
            raise ValueError("Set FC_SEC_USER_AGENT with your actual contact email")
        if refresh_remaining() is not None:
            with self._failure_lock:
                failure = self._refresh_failures.get(url)
                if failure and failure[0] <= time.monotonic():
                    del self._refresh_failures[url]
                    failure = None
            if failure:
                remaining = max(1, int(failure[0] - time.monotonic() + 0.999))
                report_progress(
                    "sec_deferred",
                    "SEC 同一资料地址刚刚获取失败，短暂延后刷新；继续使用已有资料。",
                    error_type=failure[1],
                    status=failure[2],
                    retry_seconds=remaining,
                )
                raise SecRefreshDeferred(failure[1], failure[2], remaining)
        # All acquisition jobs in this process share SEC pacing. Redirects cannot escape
        # the allowlist; the official URLs used here do not need arbitrary redirects.
        for attempt in range(3):
            while True:
                checkpoint()
                with self._network_lock:
                    wait = 0.25 - (time.monotonic() - SecFetcher._last_request)
                    if wait <= 0:
                        SecFetcher._last_request = time.monotonic()
                        break
                execution_wait(wait)
            try:
                checkpoint()
                report_progress("sec_request", "正在读取 SEC 官方资料；当前请求有超时上限。")
                response = httpx.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    timeout=bounded_timeout(30),
                    follow_redirects=False,
                )
                response.raise_for_status()
                break
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                if attempt == 2 or status is not None and status not in (408, 429) and status < 500:
                    report_progress(
                        "sec_error",
                        "SEC 连接未完成，停止本次获取。",
                        attempt=attempt + 1,
                        error_type=type(exc).__name__,
                        status=status,
                    )
                    raise
                delay = float(2**attempt)
                if status == 429:
                    retry_after = exc.response.headers.get("retry-after")
                    try:
                        delay = max(delay, float(retry_after)) if retry_after else 5.0
                    except ValueError:
                        raise exc
                    if delay > 15:
                        raise
                    with self._network_lock:
                        SecFetcher._last_request = max(
                            SecFetcher._last_request, time.monotonic() + delay - 0.25
                        )
                report_progress(
                    "sec_retry",
                    "SEC 连接暂未完成，等待重试；可取消。",
                    wait_seconds=delay,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    status=status,
                )
                execution_wait(delay)
        # CompanyFacts is mutable: retain every response version so old evidence
        # locators remain independently checkable after a metadata refresh.
        if "/companyfacts/" in parsed.path or "/submissions/" in parsed.path:
            raw = self.cache / "raw"
            raw.mkdir(exist_ok=True)
            for content in ([path.read_bytes()] if path.is_file() else []) + [response.content]:
                archived = raw / (hashlib.sha256(content).hexdigest() + ".cache")
                if not archived.exists():
                    archived.write_bytes(content)
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temp.write_bytes(response.content)
        temp.replace(path)
        return response.content


def extract_facts(
    companyfacts: dict,
    filing: Filing,
    domain: Domain = DEFAULT_DOMAIN,
    source_sha256: str | None = None,
) -> list[Fact]:
    result = []
    taxonomy = companyfacts.get("facts", {}).get("us-gaap", {})
    for metric, spec in domain.metrics.items():
        label = spec.label
        for concept in spec.concepts:
            rows = taxonomy.get(concept, {}).get("units", {}).get(spec.unit, [])
            accepted = []
            for idx, row in enumerate(rows):
                if row.get("accn") != filing.accession or row.get("form") != filing.form:
                    continue
                end = date.fromisoformat(row["end"])
                start = date.fromisoformat(row["start"]) if row.get("start") else None
                if period_kind(start, end, filing.form) is None:
                    continue
                if end > filing.period_end:
                    continue
                if filing.form in ("10-Q", "10-Q/A") and not (
                    end == filing.period_end or 357 <= (filing.period_end - end).days <= 373
                ):
                    continue  # Only this quarter/YTD and its prior-year comparison.
                accepted.append(
                    Fact(
                        id=stable_id("fact", filing.id, metric, start, end, row["val"]),
                        filing_id=filing.id,
                        ticker=filing.ticker,
                        metric=metric,
                        label=label,
                        concept="us-gaap:" + concept,
                        value=str(row["val"]),
                        unit=spec.unit,
                        fiscal_year=end.year,
                        start=start,
                        end=end,
                        source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{filing.cik}.json",
                        locator=f"facts.us-gaap.{concept}.units.{spec.unit}[{idx}]; accession={filing.accession}"
                        + (f"; source_sha256={source_sha256}" if source_sha256 else ""),
                    )
                )
            if accepted:
                result.extend({f.id: f for f in accepted}.values())
                break  # prefer one explicit canonical concept, never blend concepts.
    return result


def _foreign_heading(text: str) -> bool:
    # A cross-reference such as "please see Item 4" is not a new section.
    return len(text) < 180 and bool(
        re.match(r"^(?:Item\s+\d+[A-Z]?[.\s]|Risk Factors\s*$|Management.s Discussion)", text, re.I)
    )


_NAVIGATION_ONLY = re.compile(
    r"(?i)^(?:table of contents|part\s+[ivx]+|item\s+\d+[a-z]?\.?|signatures?)$"
)
_TITLE_LINE = re.compile(r"^[A-Z][^.!?]{20,128}$")


def _section_heading(text: str) -> bool:
    """A short title line that names the blocks below it.

    Amazon's risk factors are titled sentences without a full stop ("We Could Be
    Harmed by Data Loss or Other Security Breaches", 58 characters). The length
    floor dropped them, so the paragraphs beneath lost their risk category and a
    query for that risk could not match a section name.
    """
    stripped = text.strip()
    if not _TITLE_LINE.match(stripped):
        return False
    if stripped.isupper() or _NAVIGATION_ONLY.match(stripped):
        return False
    return len(stripped.split()) >= 4


def _content_sentence(text: str) -> bool:
    """Keep a short block only when it is a complete sentence.

    The length floor exists to drop cover-page and navigation noise, but filings
    also state product-level drivers in a single short line — Apple's FY2024
    10-K says "iPhone net sales were relatively flat during 2024 compared to
    2023." in 67 characters. Discarding every block under 130 characters removed
    45 real sentences per annual filing, after which research reported a content
    gap for something the filing does say.
    """
    if len(text) < 40 or len(text) >= 130:
        return False
    if not re.search(r"[.!?。！？]\s*$", text):
        return False
    if text.isupper() or _NAVIGATION_ONLY.match(text.strip()):
        return False
    if re.fullmatch(r"[\d\s.,%$()\-—/]+", text):
        return False
    return True


def _join_positioned_lines(soup: BeautifulSoup) -> None:
    """Reconstruct adjacent visible lines in paginated foreign annual HTML.

    SEC originals can encode each short printed line as a positioned div. The
    normal paragraph-length filter would discard nearly the entire report.
    Join only lines on the same page/container with a normal line gap; preserve
    headings and exclude all table cells. This changes the parsing tree only.
    """
    groups, current = [], []
    parent, previous_top, height = None, None, 0.0

    def flush():
        if current:
            groups.append(current[:])
            current.clear()

    for node in soup.find_all(["p", "div"]):
        if node.find(["p", "div"]):
            continue
        if node.find_parent("table"):
            flush()
            continue
        style = node.get("style", "")
        top = re.search(r"(?:^|;)\s*top\s*:\s*(\d+(?:\.\d+)?|\.\d+)pt\b", style)
        line_height = re.search(r"(?:^|;)\s*line-height\s*:\s*(\d+(?:\.\d+)?|\.\d+)pt\b", style)
        positioned = re.search(r"(?:^|;)\s*position\s*:\s*(?:var\(--position\)|absolute)", style)
        if not (top and line_height and positioned):
            flush()
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not text:
            flush()
            continue
        position, spacing = float(top[1]), float(line_height[1])
        heading = _foreign_heading(text)
        if current and (
            node.parent is not parent
            or not 0 < position - previous_top <= max(height, spacing) * 1.5
            or heading
        ):
            flush()
        current.append(node)
        parent, previous_top, height = node.parent, position, spacing
        if heading:
            flush()
    flush()
    for group in groups:
        paragraph = soup.new_tag("p")
        paragraph.string = re.sub(
            r"\s+", " ", " ".join(n.get_text(" ", strip=True) for n in group)
        ).strip()
        # Link back to an anchor that exists in the immutable original HTML.
        anchor = group[0].get("id") or (group[0].parent.find(id=True, recursive=False) or {}).get(
            "id"
        )
        if anchor:
            paragraph["id"] = anchor
        group[0].insert_before(paragraph)
        for node in group:
            node.decompose()


def extract_passages(
    html: bytes, filing: Filing, max_chars: int = 1800, *, include_tables: bool = False
) -> list[Passage]:
    return _extract_passages(
        BeautifulSoup(html, "html.parser"), filing, max_chars, include_tables=include_tables
    )


def _table_passages(
    soup: BeautifulSoup,
    filing: Filing,
    max_chars: int,
    page_numbers: dict[int, int] | None = None,
) -> list[Passage]:
    """Make table rows retrievable only when their header context is present.

    The table row remains a passage, never a typed ``Fact``. Numeric tools can
    therefore use only XBRL facts; a detached table amount cannot silently enter
    a calculation. The header, unit/caption and row are kept in one immutable
    block for retrieval and human citation.
    """
    result = []
    for table_index, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip() for cell in tr.find_all(["th", "td"])]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(cells)
        if len(rows) < 2 or len(rows[0]) < 2:
            continue
        headers = rows[0]
        caption = table.find("caption")
        unit = re.sub(r"\s+", " ", caption.get_text(" ", strip=True)).strip() if caption else ""
        anchor = table.get("id") or (table.find_parent(id=True) or {}).get("id", "")
        for row_index, cells in enumerate(rows[1:], start=1):
            text = "表格 %s | 表头：%s%s | 数据行：%s" % (
                table_index + 1,
                "；".join(headers),
                f" | 单位/说明：{unit}" if unit else "",
                "；".join(cells),
            )
            if len(text) > max_chars:
                text = text[:max_chars]
            result.append(
                Passage(
                    id=stable_id("tablepassage", filing.id, table_index, row_index, text),
                    filing_id=filing.id,
                    ticker=filing.ticker,
                    section="表格",
                    text=text,
                    source_url=filing.source_url + ("#" + anchor if anchor else ""),
                    locator=f"table {table_index + 1} row {row_index}; header bound; exact text retained",
                    page=(page_numbers or {}).get(id(table)),
                    table_context={
                        "table_index": table_index,
                        "row_index": row_index,
                        "headers": headers,
                        "row": cells,
                        "unit": unit or None,
                    },
                )
            )
    return result


def _merge_blocks(blocks: list, max_chars: int) -> list:
    """Join consecutive same-section blocks before chunking.

    Each leaf ``<p>``/``<div>`` became its own passage, so the median passage was
    232 characters — a single paragraph with no surrounding context. Ranking
    fragments at that size loses the surrounding sentences a question is phrased
    against; the page-level path in this repository uses 3,000-character chunks
    with a 4,000-character parent and reaches page@5 0.850.
    """
    merged: list = []
    for text, section, anchor, page in blocks:
        if merged and merged[-1][1] == section and len(merged[-1][0]) + 1 + len(text) <= max_chars:
            previous = merged[-1]
            merged[-1] = (previous[0] + " " + text, section, previous[2] or anchor, previous[3])
        else:
            merged.append((text, section, anchor, page))
    return merged


_FOOTER_PAGE = re.compile(r"(?:Form\s+10-K|Form\s+20-F|Annual Report)[^|]{0,20}\|\s*(\d{1,3})\s*$", re.I)


def _page_numbers(soup: BeautifulSoup) -> dict[int, int]:
    """Map each element to the printed page it sits on.

    Page breaks give a running count, but the count includes the cover and the
    table of contents: Apple's FY2024 10-K numbers 59 break-delimited pages while
    its footers print 1..57, a constant offset of two. Whenever a filing prints
    its own page numbers, the modal offset between the running count and the
    printed number is applied, so ``Passage.page`` is the number a reader sees.
    """
    pages: dict[int, int] = {}
    page = 1
    for element in soup.find_all(True):
        pages[id(element)] = page
        style = str(element.get("style") or "").lower()
        if "page-break-after" in style or element.name == "hr":
            page += 1
    offsets: Counter = Counter()
    for element in soup.find_all(["p", "div"]):
        if element.find(["p", "div"]):
            continue
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if len(text) > 90:
            continue
        match = _FOOTER_PAGE.search(text)
        if not match:
            continue
        running = pages.get(id(element))
        if running:
            offsets[running - int(match.group(1))] += 1
    if not offsets:
        return pages
    offset, support = offsets.most_common(1)[0]
    if support < 3 or support < sum(offsets.values()) * 0.8:
        return pages
    return {key: max(1, value - offset) for key, value in pages.items()}


def _extract_passages(
    soup: BeautifulSoup,
    filing: Filing,
    max_chars: int = 1800,
    *,
    include_tables: bool = False,
) -> list[Passage]:
    for node in soup.find_all(["script", "style", "ix:header", "ix:hidden", "head"]):
        node.decompose()
    if filing.form in ("20-F", "20-F/A"):
        _join_positioned_lines(soup)
    # Paragraphs/divs at leaf-block level preserve narrative; table facts use a separate path.
    blocks = []
    section = (
        "Exhibit 99.1 · company release"
        if filing.form.startswith(("8-K", "6-K")) and "EX-99.1" in filing.form
        else "Current report · company notice"
        if filing.form.startswith("8-K")
        else "Foreign issuer report · company notice"
        if filing.form.startswith("6-K")
        else "Quarterly report"
        if filing.form.startswith("10-Q")
        else "Annual report"
    )
    page_numbers = _page_numbers(soup)
    seen = set()
    for node in soup.find_all(["p", "div"]):
        if (
            node.find_parent("table")
            or node.find(["p", "div"])
            or (filing.form.startswith("6-K") and node.find("table"))
        ):
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        heading = (
            _foreign_heading(text)
            if filing.form in ("20-F", "20-F/A")
            else (
                (
                    len(text) < 180
                    and re.search(r"\bItem\s+\d|Risk Factors|Management.s Discussion", text, re.I)
                )
                or _section_heading(text)
            )
        )
        if heading:
            section = text[:160]
        if len(text) < 130 and not _content_sentence(text):
            continue
        anchor = node.get("id") or (node.find_parent(id=True) or {}).get("id", "")
        blocks.append((text, section, anchor, page_numbers.get(id(node))))
    blocks = _merge_blocks(blocks, max_chars)
    result = (
        _table_passages(soup, filing, max_chars, page_numbers) if include_tables else []
    )
    for offset, (text, section, anchor, page) in enumerate(blocks):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks, current = [], ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(
                    sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)
                )
            elif len(current) + len(sentence) > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = (current + " " + sentence).strip()
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks):
            result.append(
                Passage(
                    id=stable_id("passage", filing.id, offset, index, chunk)
                    if filing.form in ("20-F", "20-F/A")
                    else stable_id("passage", filing.id, chunk),
                    filing_id=filing.id,
                    ticker=filing.ticker,
                    section=section,
                    text=chunk,
                    source_url=filing.source_url + ("#" + anchor if anchor else ""),
                    locator=f"narrative block {offset}, chunk {index}/{len(chunks)}; exact text retained"
                    if filing.form in ("20-F", "20-F/A")
                    else f"narrative block {offset}, chunk {index}; exact text retained",
                    page=page,
                )
            )
    return list({p.id: p for p in result}.values())


def acquire_company(
    fetcher: SecFetcher,
    ticker: str,
    years: int = 2,
    offline: bool = False,
    domain: Domain = DEFAULT_DOMAIN,
    as_of: date | None = None,
    progress=None,
    forms: tuple[str, str] | None = None,
    on_entry=None,
    fiscal_years: tuple[int, ...] | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    # Resolve the annual form from the company registry. Defaulting to 10-K made
    # every 20-F issuer (TSMC, Alibaba, PDD) return zero filings, so those
    # companies could never be refreshed and never picked up parser fixes.
    ticker = ticker.upper()
    if ticker not in domain.companies:
        raise ValueError("Company is not registered; configure FC_DOMAIN_CONFIG first")
    if forms is None:
        forms = tuple(domain.annual_forms(ticker))
    allowed_years = (1, 2, 3) if forms != ("10-Q", "10-Q/A") else tuple(range(1, 13))
    if forms not in (("10-K", "10-K/A"), ("10-Q", "10-Q/A"), ("20-F", "20-F/A")) or years not in allowed_years:
        raise ValueError("Unsupported SEC reporting scope")
    target_years = set(fiscal_years or ())
    if fiscal_years is not None and (
        forms == ("10-Q", "10-Q/A") or not 1 <= len(target_years) <= 3
        or any(type(y) is not int or not 1990 <= y <= 2100 for y in fiscal_years)
    ):
        raise ValueError("Annual targets require one to three fiscal period-end years")
    spec = domain.companies[ticker]
    cik, company = spec.cik, spec.name
    submissions = json.loads(
        fetcher.fetch(f"https://data.sec.gov/submissions/CIK{cik}.json", offline, max_age=3600)
    )
    if str(submissions.get("cik", "")).zfill(10) != cik:
        raise ValueError("SEC submissions identity does not match the selected company")
    tickers = submissions.get("tickers", [])
    if tickers and ticker.replace(".", "-") not in {
        str(t).upper().replace(".", "-") for t in tickers
    }:
        raise ValueError("SEC submissions ticker does not match the selected company")
    recent = submissions["filings"]["recent"]
    cutoff = (as_of or date.today()).isoformat()

    def available(rows):
        return [
            {key: values[idx] for key, values in rows.items()}
            for idx, form in enumerate(rows.get("form", []))
            if form in forms
            and rows["filingDate"][idx] <= cutoff
            and rows.get("reportDate", [""] * len(rows["form"]))[idx]
            and (not target_years or int(rows["reportDate"][idx][:4]) in target_years)
        ]

    candidates = available(recent)
    # Historical as-of requests can lie outside the recent-submissions window.
    archives = sorted(
        submissions["filings"].get("files", []),
        key=lambda row: row.get("filingTo", ""),
        reverse=True,
    )
    for archive in archives[:20]:
        observed_years = {int(row["reportDate"][:4]) for row in candidates if row["form"] == forms[0]}
        enough = target_years <= observed_years if target_years else len(observed_years) >= years
        if enough:
            break
        if archive.get("filingFrom", "") > cutoff:
            continue
        name = archive["name"]
        if not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
            raise ValueError("Unexpected SEC submissions archive filename")
        candidates.extend(
            available(
                json.loads(fetcher.fetch("https://data.sec.gov/submissions/" + name, offline))
            )
        )
    periods = sorted(
        {row["reportDate"] for row in candidates if row["form"] == forms[0]}, reverse=True
    )
    if not target_years:
        periods = periods[:years]
    selected = sorted(
        {
            row["accessionNumber"]: row for row in candidates if row["reportDate"] in periods
        }.values(),
        key=lambda row: (row["filingDate"], row["accessionNumber"]),
        reverse=True,
    )
    if len(selected) > years * (4 if forms == ("10-Q", "10-Q/A") else 1):
        raise ValueError("Too many annual amendments for one acquisition; narrow the as-of date")
    if not selected:
        return []
    # Foreign annual originals are useful independently of numeric extraction.
    # Do not feed IFRS/native-currency or convenience USD translations into the
    # US-GAAP metric mapping. Only explicit native issuer profiles apply.
    foreign_annual = forms[0] == "20-F"
    fact_bytes = b""
    facts = {}
    if not foreign_annual:
        fact_bytes = fetcher.fetch(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", offline, max_age=3600
        )
        facts = json.loads(fact_bytes)
        if str(facts.get("cik", "")).zfill(10) != cik:
            raise ValueError("SEC financial facts identity does not match the selected company")
    result = []
    report_progress(
        "annual_inventory", "已匹配知悉日期前的申报，开始逐份保存。",
        preparation_progress={"total_reports": len(selected), "completed_reports": 0},
    )
    for idx, row in enumerate(selected):
        if date.fromisoformat(row["reportDate"]) > date.fromisoformat(row["filingDate"]):
            raise ValueError("Report period cannot end after its publication date")
        form, accession = row["form"], row["accessionNumber"]
        document = row["primaryDocument"]
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+\.html?", document, re.I
        ):
            raise ValueError(
                "Unsupported SEC annual/quarterly document path; HTML reports required"
            )
        if progress:
            progress(
                "downloading",
                f"正在下载 {company} {row['reportDate']} {form}（{idx + 1}/{len(selected)}）",
            )
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{document}"
        )
        html = fetcher.fetch(
            url,
            offline,
            **({"force_refresh": True} if force_refresh else {}),
        )
        filing = Filing(
            id=ticker + ":" + accession,
            ticker=ticker,
            company=company,
            cik=cik,
            accession=accession,
            form=form,
            filed=row["filingDate"],
            period_end=row["reportDate"],
            source_url=url,
            content_sha256=hashlib.sha256(html).hexdigest(),
        )
        if foreign_annual:
            tree = BeautifulSoup(html, "html.parser")
            extracted = _extract_ifrs_facts(tree, filing, domain)
            passages = _extract_passages(tree, filing, include_tables=True)
            statement_facts: list[StatementFact] = extract_inline_statement_facts(html, filing)
        else:
            extracted = extract_facts(
                facts,
                filing,
                domain,
                hashlib.sha256(fact_bytes).hexdigest() if forms[0] == "10-Q" else None,
            )
            passages = extract_passages(html, filing, include_tables=True)
            statement_facts = extract_statement_facts(
                facts,
                filing,
                hashlib.sha256(fact_bytes).hexdigest() if forms[0] == "10-Q" else None,
            )
            statement_facts.extend(extract_inline_statement_facts(html, filing))
        present = {f.id for f in extracted}
        if not foreign_annual:
            extracted.extend(
                f for f in extract_cash_facts(html, filing, domain) if f.id not in present
            )
        result.append(
            {
                "filing": filing.model_dump(mode="json"),
                "facts": [f.model_dump(mode="json") for f in extracted],
                "statement_facts": [f.model_dump(mode="json") for f in statement_facts],
                "passages": [p.model_dump(mode="json") for p in passages],
            }
        )
        if on_entry:
            on_entry(result[-1])
    return result
