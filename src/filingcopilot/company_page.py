"""Deterministic company source blocks assembled from observed evidence.

The page is a source map, not a hidden investment opinion. Each block carries its
own date, evidence IDs and gap so a failed market or filing source cannot erase
the other blocks. Full prose remains the existing evidence-reviewed company report.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import Field

from .company_modules import MODULES
from .execution import ResearchStopped, checkpoint, refresh_scope
from .market import symbol
from .models import StrictModel

PAGE_SECTIONS = {
    "business": ("商业模式", "business products services segments customers"),
    "operating_change": (
        "经营变化",
        "net sales operating income increased decreased guidance outlook",
    ),
    "cashflow": ("现金流与资本投入", "operating cash flow capital expenditures working capital"),
    "debt": ("负债与流动性", "debt borrowings interest liquidity maturities leverage"),
    "risk": ("风险", "risk factors regulatory demand concentration uncertainties"),
    "recent_events": ("近期事件", "announcement results filing guidance"),
    "valuation": ("估值", "diluted earnings per share valuation assumptions"),
    "ownership": ("上市股份与币种", "American depositary shares ADS ordinary shares currency translation"),
    "audit": ("审计报告原文", "present fairly opinion financial statements qualified adverse disclaimer going concern"),
}
PageKey = Literal[
    "business", "operating_change", "cashflow", "debt", "risk", "recent_events", "valuation", "ownership", "audit"
]


def audit_source_order(passage: dict, filing: dict) -> tuple[bool, bool]:
    """Prefer the actual opinion text and its reporting year; never classify it."""
    text = passage['text']
    opinion = bool(re.search(r'in our opinion|we have audited|we do not express an opinion|our opinion is qualified|we express an adverse opinion|we disclaim an opinion', text, re.I))
    current = bool(re.search(r'(?<!\d)'+re.escape(filing['period_end'][:4])+r'(?!\d)', text))
    return opinion and current, opinion


class ModuleBrief(StrictModel):
    """The fixed-format contract for this section's written summary.

    ``question`` is the research question sent with ``module``; ``boundary`` and
    ``empty_gap`` are rendered verbatim (browser translation applies when the
    interface language differs). The full brief text lives server-side in
    ``company_modules``; the payload carries only what the page needs.
    """

    kind: Literal["analysis", "factual"]
    question: str
    boundary: str
    empty_gap: str


class PageSection(StrictModel):
    key: PageKey
    title: str
    status: Literal["ready", "partial", "gap"]
    as_of: date
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    source_dates: list[str] = Field(default_factory=list, max_length=12)
    gap: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=300)
    module: ModuleBrief | None = None


def _source_date(item: dict) -> str | None:
    filing = item.get("filing") or {}
    return (
        filing.get("filed")
        or item.get("available_at")
        or item.get("snapshot", {}).get("retrieved_at")
    )


def build_company_page(
    service,
    ticker: str,
    as_of: date,
    *,
    refresh: bool = False,
    section: str | None = None,
    include_market: bool = True,
) -> dict:
    """Build a replayable evidence map with local failure boundaries."""
    ticker = symbol(ticker)
    if as_of > date.today() or section is not None and section not in PAGE_SECTIONS:
        raise ValueError("公司研究日期或区块无效。")
    selected = [section] if section else list(PAGE_SECTIONS)
    capabilities = service.capabilities(ticker)
    preparations = []
    source_warnings: dict[str, list[str]] = {}
    missing_filings = not service.corpus.catalog(as_of, ticker)
    if (refresh or missing_filings) and capabilities["filings_supported"] is not False:
        jobs = []
        financial = set(selected) - {"recent_events"}
        if financial:
            jobs.append(("annual", service.discovery.refresh_annual, financial))
        if (
            set(selected) & {"operating_change", "cashflow", "debt"}
            and not capabilities["annual_only"]
        ):
            jobs.append(
                (
                    "quarterly",
                    service.discovery.prepare_quarterly,
                    set(selected) & {"operating_change", "cashflow", "debt"},
                )
            )
        if set(selected) & {"operating_change", "recent_events"}:
            if capabilities["company_updates"]:
                jobs.append(
                    (
                        "official_updates",
                        service.discovery.prepare_updates,
                        set(selected) & {"operating_change", "recent_events"},
                    )
                )
            if "recent_events" in selected and capabilities["external_updates"]:
                jobs.append(
                    (
                        "external_updates",
                        service.discovery.prepare_external_updates,
                        {"recent_events"},
                    )
                )
        for name, prepare, affected in jobs:
            checkpoint()
            try:
                with refresh_scope(min(8, service.settings.refresh_timeout)):
                    result = prepare(ticker, as_of)
                warnings = list(result.get("warnings", []))
                if result.get("warning"):
                    warnings.append(result["warning"])
                if result.get("checked") is False and not warnings:
                    warnings.append("本次来源检查未完成，已有资料不代表最新。")
                preparations.append(
                    {"source": name, "checked": result.get("checked", False), "warnings": warnings}
                )
            except ResearchStopped:
                raise
            except Exception as exc:
                warnings = [f"来源检查未完成（{type(exc).__name__}），保留已有原文，可单独重试。"]
                preparations.append({"source": name, "checked": False, "warnings": warnings})
            for key in affected:
                source_warnings.setdefault(key, []).extend(warnings)
    service.enrich_cached_facts(ticker)
    tools = service.tools(as_of)
    overview = None
    if section is None:
        checkpoint()
        try:
            overview = tools.get_company_overview(ticker)
        except ResearchStopped:
            raise
        except Exception:
            overview = {"found": False, "missing": ["年度概览未取得；可查看各区块已有材料。"]}
    sections: list[PageSection] = []
    gaps: list[str] = []
    catalog_filings = service.corpus.catalog(as_of, ticker)
    financial_filings = [
        f
        for f in catalog_filings
        if f["form"] in ("10-K", "10-K/A", "20-F", "20-F/A", "10-Q", "10-Q/A")
    ]
    annual_filings = [
        f for f in financial_filings if f["form"] in ("10-K", "10-K/A", "20-F", "20-F/A")
    ]
    def latest(rows):
        return max(rows, key=lambda f: (f["period_end"], f["filed"], f["id"]), default=None)

    def add(key: str, evidence_ids: list[str], gap: str = "", note: str = "") -> None:
        evidence = [tools.ledger[item_id] for item_id in evidence_ids if item_id in tools.ledger]
        dates = list(dict.fromkeys(d for d in (_source_date(item) for item in evidence) if d))
        gap = " ".join(dict.fromkeys([*([gap] if gap else []), *source_warnings.get(key, [])]))[
            :300
        ]
        status = "ready" if evidence and not gap else "partial" if evidence else "gap"
        contract = MODULES.get(key)
        section = PageSection(
            key=key,
            title=PAGE_SECTIONS[key][0],
            status=status,
            as_of=as_of,
            evidence_ids=[item["id"] for item in evidence],
            source_dates=dates,
            gap=gap,
            note=note or "以下是来源原文材料；尚未由模型生成或审查完整分析。",
            module=(
                ModuleBrief(
                    kind=contract.kind,
                    question=contract.question,
                    boundary=contract.boundary,
                    empty_gap=contract.empty_gap,
                )
                if contract
                else None
            ),
        )
        sections.append(section)
        if gap:
            gaps.append(f"{section.title}：{gap}")

    for key in (key for key in selected if key not in ("recent_events", "valuation")):
        checkpoint()
        try:
            filing = latest(annual_filings if key in ("business", "risk", "ownership", "audit") else financial_filings)
            scope_note = ""
            if filing:
                section_title = (
                    (
                        "Information on the Company"
                        if filing["form"].startswith("20-F")
                        else "Business"
                    )
                    if key == "business"
                    else "Risk Factors"
                    if key == "risk"
                    else None
                )
                hits = tools.retriever.search(
                    PAGE_SECTIONS[key][1],
                    as_of,
                    ticker,
                    8,
                    filing_id=filing["id"],
                    lexical_only=True,
                    section_title=section_title,
                ).get("hits", [])
                if section_title and not hits:
                    hits = tools.retriever.search(
                        PAGE_SECTIONS[key][1],
                        as_of,
                        ticker,
                        8,
                        filing_id=filing["id"],
                        lexical_only=True,
                    ).get("hits", [])
                    scope_note = (
                        "未匹配对应章标题，以下为该申报中的关键词材料，需核对原文范围。"
                        if hits
                        else ""
                    )
                for hit in hits:
                    item = service.corpus.evidence(hit["passage"]["id"], as_of)
                    if item:
                        tools.ledger[item["id"]] = item
            else:
                hits = []
            # Identical paragraphs across contexts do not create useful extra sources.
            unique = {}
            if key == 'audit' and filing:
                hits.sort(key=lambda hit: audit_source_order(hit['passage'],filing),reverse=True)
            for hit in hits:
                unique.setdefault(" ".join(hit["passage"]["text"].split()), hit["passage"]["id"])
            ids = list(unique.values())[:3]
            add(
                key,
                ids,
                "申报原文检索未返回足够证据；请准备或刷新对应年报/季报。"
                if not ids
                else scope_note,
                note=("上市股份与币种须按原文配对；人民币普通股 EPS 不能直接搭配美元 ADS 股价计算市盈率。未使用当前汇率推算 ADS EPS。"
                      if key == "ownership" else
                      "以下是审计报告原文候选，不是自动审计结论。年报可能包含不同年度、审计师和财务报表或内部控制意见，需核对具体覆盖范围。"
                      if key == "audit" else ""),
            )
        except ResearchStopped:
            raise
        except Exception as exc:
            add(key, [], f"本块来源读取失败（{type(exc).__name__}），可单独重试。")

    if "recent_events" in selected:
        checkpoint()
        try:
            updates = tools.get_company_updates(ticker)
            ids = []
            for group in updates.get("sections", {}).values():
                for item in group:
                    ids.extend(id_ for id_ in item.get("evidence_ids", []) if id_ in tools.ledger)
            add(
                "recent_events",
                list(dict.fromkeys(ids))[:8],
                "未取得知悉日期前的官方公告/事件。" if not ids else "",
            )
        except ResearchStopped:
            raise
        except Exception as exc:
            add("recent_events", [], f"近期事件来源读取失败（{type(exc).__name__}），可单独重试。")

    if "valuation" in selected:
        checkpoint()
        valuation_ids: list[str] = []
        valuation_gap = ""
        try:
            if include_market:
                market = tools.get_stock_market(ticker)
                if market.get("evidence"):
                    valuation_ids.append(market["evidence"]["id"])
                annual = tools.get_stock_valuation(ticker)
                if annual.get("evidence"):
                    valuation_ids.append(annual["evidence"]["id"])
                if not annual.get("found"):
                    valuation_gap = annual.get("reason", "尚无可用的匹配 EPS 估值。")[:300]
            else:
                filings = [
                    f
                    for f in service.corpus.catalog(as_of, ticker)
                    if f["form"] in ("10-K", "10-K/A", "20-F", "20-F/A")
                ]
                if filings:
                    year = int(max(f["period_end"] for f in filings)[:4])
                    annual = tools.get_financial_fact(ticker, "diluted_eps", year)
                    if annual.get("evidence"):
                        valuation_ids.append(annual["evidence"]["id"])
                valuation_gap = "此区块只读取已保存的年度 EPS；报价与市盈率在行情卡片独立加载。"
            if not valuation_ids and not valuation_gap:
                valuation_gap = "当前报价或年度 EPS 估值来源不可用；不能输出单一目标价。"
        except ResearchStopped:
            raise
        except Exception as exc:
            valuation_gap = f"估值来源读取失败（{type(exc).__name__}）；可单独重试。"
        add(
            "valuation",
            list(dict.fromkeys(valuation_ids)),
            valuation_gap,
            "可复核输入与用户情景假设分开；这里只整理材料，不作估值判断。",
        )
    available_forms = sorted({f["form"] for f in catalog_filings})
    coverage = {
        "available_forms": available_forms,
        "annual_forms": sorted({f["form"] for f in annual_filings}),
        "quarterly_forms": sorted(
            {f["form"] for f in financial_filings if f["form"] in ("10-Q", "10-Q/A")}
        ),
        "update_forms": sorted(
            {f["form"] for f in catalog_filings if f["form"].startswith(("8-K", "6-K"))}
        ),
        "official_updates": bool(capabilities.get("company_updates", False)),
        "external_updates": bool(capabilities.get("external_updates", False)),
        "limitations": [
            "分部历史金额、管理层指引与电话会需要相应结构化或已登记来源；本页不会从缺失文件推断。",
            "未登记的 6-K、交易所公告、新闻或 PDF 不会被当作已覆盖来源。",
        ],
    }
    return {
        "schema": "filingcopilot.company-page/v1",
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "refresh_requested": refresh,
        "source_preparation": preparations,
        "analysis_generated": False,
        "source_coverage": coverage,
        "annual_overview": overview,
        "sections": [section.model_dump(mode="json") for section in sections],
        "gaps": list(dict.fromkeys(gaps)),
        "evidence": list(tools.ledger.values()),
        "note": "九个模块的状态、日期、证据和缺口独立维护；页面本身不构成投资建议或目标价。",
    }
