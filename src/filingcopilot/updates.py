"""Dated official company notices, bound to immutable SEC filing and exhibit bytes."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .fundamentals import is_stock_question
from .ingestion import extract_passages
from .models import Filing

UPDATE_SCOPE = (
    "近 90 天最多五次 SEC 8-K / 8-K/A 公司公告及随附 EX-99.1 原文；"
    "覆盖业绩发布和重大事项，不包含全部媒体新闻、传闻或盘中消息。"
    "知悉日期按公开申报日筛选，包含当日披露，不能用于当日盘中回测。"
)


def update_forms(listing) -> tuple[str, ...]:
    """Only explicitly registered foreign listings advertise 6-K notice acquisition."""
    if listing and (
        not listing.filings_supported or (listing.annual_form == "20-F" and not listing.update_form)
    ):
        return ()
    base = listing.update_form if listing and listing.update_form else "8-K"
    return base, base + "/A"


def update_scope(forms: tuple[str, ...]) -> str:
    if not forms:
        return "此上市证券的公司公告来源尚未接入。"
    if forms[0] == "8-K":
        return UPDATE_SCOPE
    return (
        "近 90 天最多五次 SEC 6-K / 6-K/A 官方公司公告及明确关联的 EX-99.1 原文。"
        "显示原文摘录，不包含全部媒体新闻；报告标注日期不等于事件发生日。"
        "知悉范围按 SEC 公开日筛选，包含当日披露，不能用于当日盘中回测；"
        "未从这些公告重建季度财务或 TTM。"
    )


def wants_update_summary(question: str) -> bool:
    """An explicit summary opt-in keeps ordinary notice lookup model-free for 6-K."""
    return (
        is_updates_question(question)
        and not is_stock_question(question)
        and bool(re.search(r"摘要|中文解读|中文总结|中文概括|summari[sz]e|summary", question, re.I))
    )


def is_stock_updates_question(question: str) -> bool:
    """A combined assessment keeps financial tools and explicitly covers notices."""
    return is_stock_question(question) and is_updates_question(question)


def is_updates_question(question: str) -> bool:
    return bool(
        re.search(
            r"新闻|公告|最近.{0,6}(?:消息|发生|动态)|最新.{0,6}(?:消息|动态)|"
            r"近期.{0,6}(?:消息|动态)|业绩发布|电话会|逐字稿|earnings (?:release|call)|transcript|\bnews\b|\b[68]-K\b|"
            r"\brecent (?:company )?updates\b|\bcompany (?:announcements|disclosures)\b",
            question,
            re.I,
        )
    )


def update_identity(event: dict) -> str:
    return event.get("accession") or "external:" + event["id"]


def summary_events(overview: dict, question: str, limit: int = 5) -> list[dict]:
    """Choose items without merging official and external evidence identities."""
    official = [e for e in overview["events"] if e["evidence_ids"]]
    sections = overview.get("sections", {})
    transcripts = sections.get("transcripts", [])
    news = sections.get("news", [])
    hkex = [*sections.get("hkex_en", []), *sections.get("hkex_zh", [])]
    cninfo = sections.get("cninfo", [])
    if re.search(r"电话会|逐字稿|earnings call|transcript", question, re.I):
        return transcripts[:limit]
    items = (
        [*news, *hkex, *cninfo, *official, *transcripts]
        if re.search(r"新闻|\bnews\b", question, re.I)
        else [*official, *hkex, *cninfo, *transcripts, *news]
    )
    return items[:limit]


def _scope(as_of: date, days: int, limit: int):
    if as_of > date.today() or days != 90 or not 1 <= limit <= 5:
        raise ValueError("公告查询支持知悉日期前 90 天、最多五次申报。")
    return (as_of - timedelta(days=days - 1)).isoformat(), as_of.isoformat()


def _rows(
    payload: dict, start: str, end: str, metadata_url="", metadata_sha="", forms=("8-K", "8-K/A")
) -> list[dict]:
    columns = ("form", "filingDate", "accessionNumber", "reportDate", "primaryDocument")
    count = len(payload.get("form", []))
    if any(not isinstance(payload.get(key), list) or len(payload[key]) != count for key in columns):
        raise ValueError("SEC 公告元数据列不完整。")
    if any(not isinstance(value, str) for key in columns for value in payload[key]):
        raise ValueError("SEC 公告元数据类型无效。")
    return [
        {
            **{key: payload[key][idx] for key in columns},
            "metadata_url": metadata_url,
            "metadata_sha": metadata_sha,
        }
        for idx in range(count)
        if payload["form"][idx] in forms and start <= payload["filingDate"][idx] <= end
    ]


def _exhibit(html: bytes, primary_url: str) -> str | None:
    """Follow only an EX-99.1 link inside this exact accession directory."""
    base = primary_url.rsplit("/", 1)[0] + "/"
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href, label = anchor["href"], anchor.get_text(" ", strip=True)
        row = anchor.find_parent("tr")
        row_label = row.get_text(" ", strip=True) if row else ""
        if not re.search(r"99[._-]?1\b", href + " " + label + " " + row_label, re.I):
            continue
        if re.search(r"99[._-]?2\b", href + " " + label, re.I):
            continue
        url = urljoin(primary_url, href)
        parsed = urlsplit(url)
        name = parsed.path.rsplit("/", 1)[-1]
        if (
            url.rsplit("/", 1)[0] + "/" == base
            and not parsed.query
            and not parsed.fragment
            and re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", name, re.I)
            and url != primary_url
        ):
            return url
    return None


def acquire_updates(
    fetcher, ticker, domain, as_of: date, days=90, limit=5, forms=("8-K", "8-K/A"), on_entry=None
) -> dict:
    start, end = _scope(as_of, days, limit)
    company = domain.companies[ticker]
    metadata_url = f"https://data.sec.gov/submissions/CIK{company.cik}.json"
    metadata_bytes = fetcher.fetch(metadata_url, max_age=3600)
    submissions = json.loads(metadata_bytes)
    if str(submissions.get("cik", "")).zfill(10) != company.cik:
        raise ValueError("SEC 公告公司身份不匹配。")
    tickers = submissions.get("tickers", [])
    if tickers and ticker.replace(".", "-") not in {
        str(t).upper().replace(".", "-") for t in tickers
    }:
        raise ValueError("SEC 公告股票代码不匹配。")
    candidates = _rows(
        submissions["filings"]["recent"],
        start,
        end,
        metadata_url,
        hashlib.sha256(metadata_bytes).hexdigest(),
        forms,
    )
    archives = sorted(
        submissions["filings"].get("files", []), key=lambda r: r.get("filingTo", ""), reverse=True
    )
    warnings = []
    archives = [
        a for a in archives if a.get("filingFrom", "") <= end and a.get("filingTo", "") >= start
    ]
    if len(archives) > 20:
        warnings.append(
            "历史公告索引超过单次检查预算，窗口覆盖可能不完整；可调整知悉日期或直接查看 SEC 申报列表。"
        )
    for archive in archives[:20]:
        if len(candidates) >= limit:
            oldest = sorted(r["filingDate"] for r in candidates)[-limit]
            if archive.get("filingTo", "") < oldest:
                continue
        name = archive["name"]
        if not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
            raise ValueError("SEC 公告历史索引文件名无效。")
        archive_url = "https://data.sec.gov/submissions/" + name
        archive_bytes = fetcher.fetch(archive_url)
        candidates.extend(
            _rows(
                json.loads(archive_bytes),
                start,
                end,
                archive_url,
                hashlib.sha256(archive_bytes).hexdigest(),
                forms,
            )
        )
    candidates = sorted(
        {r["accessionNumber"]: r for r in candidates}.values(),
        key=lambda r: (r["filingDate"], r["accessionNumber"]),
        reverse=True,
    )[:limit]
    entries = []
    for row in candidates:
        accession, document = row["accessionNumber"], row["primaryDocument"]
        event = row["reportDate"]
        if (
            not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", document, re.I)
            or not event
            or date.fromisoformat(event) > date.fromisoformat(row["filingDate"])
        ):
            warnings.append(
                f"{row['filingDate']} 的一份公告路径或报告标注日期不可用，未纳入原文研究。"
            )
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{int(company.cik)}/{accession.replace('-', '')}/{document}"
        sources = [(url, row["form"], ticker + ":" + accession)]
        primary_sha = None
        for source_url, form, filing_id in sources:
            try:
                html = fetcher.fetch(source_url)
                filing = Filing(
                    id=filing_id,
                    ticker=ticker,
                    company=company.name,
                    cik=company.cik,
                    accession=accession,
                    form=form,
                    filed=row["filingDate"],
                    period_end=event,
                    source_url=source_url,
                    content_sha256=hashlib.sha256(html).hexdigest(),
                )
                passages = extract_passages(html, filing)
                if source_url == url:
                    primary_sha = filing.content_sha256
                if hasattr(fetcher, "cache"):
                    _save_metadata(fetcher.cache, row, filing, primary_sha)
                entry = {"filing": filing, "passages": passages}
                entries.append(entry)
                if on_entry:
                    on_entry(entry)
                if source_url == url:
                    exhibit = _exhibit(html, url)
                    if exhibit:
                        sources.append((exhibit, row["form"] + " EX-99.1", filing_id + ":EX-99.1"))
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError, OSError) as exc:
                reason = (
                    f"HTTP {exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else type(exc).__name__
                )
                warnings.append(
                    f"公开于 {row['filingDate']} 的 {form} 原文未取齐（{reason}），可稍后重试。"
                )
    return {
        "entries": entries,
        "warnings": warnings,
        "window_start": start,
        "scope": update_scope(forms),
    }


def _save_metadata(cache, row, filing, primary_sha):
    path = cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".metadata.json")
    if path.exists():
        return  # immutable version binding, like the filing itself
    payload = {
        "metadata_url": row["metadata_url"],
        "metadata_sha": row["metadata_sha"],
        "primary_sha": primary_sha,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    temp.replace(path)


def verify_update_metadata(cache, filing: Filing) -> bool | None:
    """Recheck public/event dates and exhibit identity against independently supplied raw cache."""
    path = cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".metadata.json")
    if not path.is_file():
        return None
    metadata = json.loads(path.read_text(encoding="utf-8"))
    url, sha = metadata["metadata_url"], metadata["metadata_sha"]
    if not re.fullmatch(r"[a-f0-9]{64}", sha) or not re.fullmatch(
        r"https://data\.sec\.gov/submissions/CIK" + filing.cik + r"(?:-submissions-\d+)?\.json", url
    ):
        return False
    raw = cache / "raw" / (sha + ".cache")
    if not raw.is_file():
        cached = cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")
        if not cached.is_file():
            return None
        raw = cached
    content = raw.read_bytes()
    if hashlib.sha256(content).hexdigest() != sha:
        return False
    payload = json.loads(content)
    if "filings" in payload:
        if str(payload.get("cik", "")).zfill(10) != filing.cik:
            return False
        payload = payload["filings"]["recent"]
    rows = _rows(
        payload,
        filing.filed.isoformat(),
        filing.filed.isoformat(),
        forms=("8-K", "8-K/A", "6-K", "6-K/A"),
    )
    matches = [r for r in rows if r["accessionNumber"] == filing.accession]
    if len(matches) != 1:
        return False
    row = matches[0]
    primary_url = f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/{filing.accession.replace('-', '')}/{row['primaryDocument']}"
    if row["reportDate"] != filing.period_end.isoformat():
        return False
    if "EX-99.1" not in filing.form:
        return filing.form == row["form"] and filing.source_url == primary_url
    primary = cache / (hashlib.sha256(primary_url.encode()).hexdigest() + ".cache")
    return (
        primary.is_file()
        and filing.form == row["form"] + " EX-99.1"
        and hashlib.sha256(primary.read_bytes()).hexdigest() == metadata["primary_sha"]
        and _exhibit(primary.read_bytes(), primary_url) == filing.source_url
    )


def updates_overview(tools, ticker: str, days=90, limit=5) -> dict:
    start, _ = _scope(tools.as_of, days, limit)
    from .stock_directory import StockDirectory

    directory = getattr(tools.market, "directory", None) or StockDirectory()
    forms = update_forms(directory.listing(ticker))
    filings = [
        f
        for f in tools.corpus.catalog(tools.as_of, ticker.upper())
        if f["form"].split(" ")[0] in forms and f["filed"] >= start
    ]
    groups = {}
    for filing in filings:
        groups.setdefault(filing["accession"], []).append(filing)
    ordered = sorted(
        groups.values(), key=lambda g: (g[0]["filed"], g[0]["accession"]), reverse=True
    )
    events = []
    for group in ordered[:limit]:
        # Exhibits usually contain the substantive release; primary notices remain linked.
        sources = sorted(group, key=lambda f: "EX-99.1" not in f["form"])
        passages = [
            p
            for f in sources
            for p in tools.corpus.passages(tools.as_of, ticker.upper(), filing_id=f["id"])
        ]
        useful = [
            p
            for p in passages
            if not re.search(
                r"Pursuant to the requirements|thereunto duly authorized|/s/|shall not be deemed|forward-looking statements|incorporated (?:herein )?by reference|qualified in (?:their|its) entirety|safe harbor|signature|not a solicitation|^Check the appropriate|^ITEM\s+\d|^Indicate by check|^Emerging growth|^Title of each class",
                p.text,
                re.I,
            )
        ]
        # A substantive headline/paragraph is more useful than exhibit boilerplate.
        picks = sorted(
            useful if forms and forms[0] == "6-K" else useful or passages,
            key=lambda p: (
                "EX-99.1" not in next(f["form"] for f in sources if f["id"] == p.filing_id),
                not bool(
                    re.search(
                        r"announc|results|launch|appoint|agreement|dividend|acquir|revenue|sales|income",
                        p.text,
                        re.I,
                    )
                ),
                int(re.search(r"narrative block (\d+)", p.locator).group(1)),
            ),
        )[:1]
        ids = []
        for passage in picks:
            evidence = tools.corpus.evidence(passage.id, tools.as_of)
            tools.ledger[passage.id] = evidence
            ids.append(passage.id)
        events.append(
            {
                "accession": group[0]["accession"],
                "filed": group[0]["filed"],
                "event_date": None if forms[0] == "6-K" else group[0]["period_end"],
                **(
                    {"report_date": group[0]["period_end"], "date_basis": "sec_report_date"}
                    if forms[0] == "6-K"
                    else {}
                ),
                "documents": sources,
                "evidence_ids": ids,
                "excerpts": [{"id": p.id, "text": p.text, "filing_id": p.filing_id} for p in picks],
            }
        )
    external = {"transcripts": [], "news": [], "hkex_en": [], "hkex_zh": [], "cninfo": []}
    for update in tools.corpus.external_updates(tools.as_of, ticker.upper(), limit=100):
        if update.available_day.isoformat() < start:
            continue
        key = (
            "transcripts"
            if update.source_type == "transcript"
            else "news"
            if update.source_type == "news"
            else "hkex_en"
            if update.source_type == "hkex" and update.language == "en"
            else "hkex_zh"
            if update.source_type == "hkex"
            else "cninfo"
        )
        # A refreshed feed may retain several immutable versions of one item.
        # Show only the most recently retrieved locally available version.
        if len(external[key]) >= limit or any(e["source_url"] == update.source_url for e in external[key]):
            continue
        evidence = tools.corpus.evidence(update.id, tools.as_of)
        tools.ledger[update.id] = evidence
        external[key].append({**update.model_dump(mode="json"), "evidence_ids": [update.id]})
    return {
        "found": any(e["evidence_ids"] for e in events) or any(external.values()),
        "ticker": ticker.upper(),
        "window_start": start,
        "as_of": tools.as_of.isoformat(),
        "events": events,
        "sections": {"official": events, **external},
        "external_scope": "电话会、新闻、HKEXnews 和巨潮资讯公告均为独立来源；HKEX 中英文版本分区保存，公开时间采用来源时间。未提供公开时间的逐字稿仅在首次取得后可用，事件日不替代公开日。这里提供原文摘录，并非完整电话会、全部新闻或全部公告。",
        "scope": update_scope(forms)
        + (
            "；已登记 HKEXnews 公告（中英文双版本，年报指标另行处理）"
            if any(external[key] for key in ("hkex_en", "hkex_zh"))
            else ""
        )
        + ("；已登记巨潮资讯中文公告（年报指标另行处理）" if external["cninfo"] else ""),
        "next_step": (
            "可打开 6-K 原文及附件核对完整内容；经营数字可查询已接入的年度财务与现金流。"
            if forms and forms[0] == "6-K"
            else "可打开原始公告及附件；想看经营变化，可追问最新季度财务或 TTM 现金流。"
        )
        if events
        else "窗口内没有已保存的可读公告。请自动刷新 SEC，或调整知悉日期查看较早披露。",
    }
