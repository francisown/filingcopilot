"""Build an editable corpus-anchored retrieval gold set.

The first set used placeholder self-IDs.  This revision chooses real passage
IDs from the frozen corpus and writes natural bilingual prompts around them.
It is still an editorial calibration set, not a human-validated accuracy claim.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus

COMPANIES = {
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "TSM": "TSMC",
}
CATEGORIES = {
    "business": ("business products services customers", "业务、产品、服务和客户"),
    "growth": ("revenue operating income increase decrease", "收入和营业利润变化原因"),
    "cashflow": ("cash flows capital expenditures operating activities", "经营现金流和资本开支"),
    "risk": ("risk factors demand regulatory supply", "风险、需求和监管因素"),
    "competition": ("competition competitive advantages", "竞争和竞争优势"),
    "guidance": ("outlook expect future demand", "展望、预期和需求"),
    "segments": ("segment geographic revenue", "分部、地域和收入"),
    "margin": ("gross margin operating margin profit", "毛利率、营业利润率和盈利"),
    "quarter": ("quarter three months six months", "季度和年内累计期间"),
    "debt": ("debt liquidity obligations", "债务、流动性和义务"),
}
EN_QUERY_TEMPLATES = (
    "Which {anchor} did {company} report in FY{year}?",
    "Review {company}'s FY{year} filing: where does it discuss {anchor}?",
    "For {company} FY{year}, find filed evidence about {anchor}.",
    "What does {company}'s FY{year} annual filing say about {anchor}?",
    "Locate the {anchor} disclosure in {company}'s FY{year} filing.",
)
ZH_QUERY_TEMPLATES = (
    "{company} FY{year} 年报里，哪些段落说明{zh_anchor}？",
    "请从 {company} FY{year} 年报找出{zh_anchor}的原文依据。",
    "在 {company} FY{year} 的申报中，{zh_anchor}是怎样披露的？",
    "核对 {company} FY{year} 年报关于{zh_anchor}的表述。",
    "我想查 {company} FY{year} 年报的{zh_anchor}，请给出原文证据。",
)


def annual_filings(corpus: Corpus, ticker: str) -> list[dict]:
    rows = corpus.catalog(date(2026, 10, 10), ticker)
    annual = [
        row
        for row in rows
        if row.get("form") in {"10-K", "20-F"} and row.get("period_end")
    ]
    return sorted(annual, key=lambda row: (row["period_end"], row["id"]))


def choose_passage(passages, filing_ids: set[str], anchor: str, offset: int):
    terms = anchor.split()
    candidates = [p for p in passages if p.filing_id in filing_ids]
    ranked = sorted(
        candidates,
        key=lambda p: (-sum(term.casefold() in (p.section + " " + p.text).casefold() for term in terms), p.id),
    )
    return ranked[offset % len(ranked)] if ranked else None


def build() -> dict:
    home = Path(".filingcopilot")
    corpus = Corpus(home)
    items = []
    serial = 1
    for ticker, company in COMPANIES.items():
        filings = annual_filings(corpus, ticker)
        if not filings:
            raise RuntimeError(f"no annual filings for {ticker}")
        all_passages = corpus.passages(date(2026, 10, 10), ticker=ticker)
        for category, (anchor, zh_anchor) in CATEGORIES.items():
            for repeat in range(4):
                filing = filings[(repeat + len(category)) % len(filings)]
                year = int(filing["period_end"][:4])
                passage = choose_passage(all_passages, {filing["id"]}, anchor, repeat)
                if passage is None:
                    raise RuntimeError(f"no passage for {ticker} {filing['id']} {category}")
                language = "zh" if serial % 2 else "en"
                if language == "zh":
                    query = ZH_QUERY_TEMPLATES[repeat % len(ZH_QUERY_TEMPLATES)].format(
                        company=company, year=year, zh_anchor=zh_anchor
                    )
                else:
                    query = EN_QUERY_TEMPLATES[repeat % len(EN_QUERY_TEMPLATES)].format(
                        company=company, year=year, anchor=anchor
                    )
                items.append(
                    {
                        "id": f"gold-v2-{serial:04d}",
                        "language": language,
                        "ticker": ticker,
                        "company": company,
                        "fiscal_year": year,
                        "query": query,
                        "expected_behavior": "retrieve_cited_passage_with_header_scope",
                        "gold_context": {
                            "section": category,
                            "required_header_context": bool(passage.table_context),
                            "anchor_terms": anchor.split(),
                            "editorial_source": "corpus-anchored editorial revision",
                        },
                        "expected_passage_ids": [passage.id],
                    }
                )
                serial += 1
    return {
        "schema": "filingcopilot.retrieval-gold/v2",
        "version": "2026-10-11-editorial",
        "count": len(items),
        "notes": [
            "Passage IDs are real IDs from the frozen local corpus, replacing v1 placeholder self-IDs.",
            "Prompts mix Chinese and English and use natural question forms; this remains an editorial set, not human-validated truth.",
            "The set is only meaningful with the same corpus snapshot used to build it.",
        ],
        "items": items,
    }


if __name__ == "__main__":
    path = Path("evaluation/retrieval_gold_v2.json")
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
