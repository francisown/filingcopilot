"""Evaluate FilingCopilot retrieval on the public FinanceBench subset.

Important scope note: the public FinanceBench rows expose the *evidence pages*,
not every page of every filing. This script therefore measures evidence-page
retrieval, not full-filing retrieval. It is still useful: it checks whether the
retriever can find the page and snippet that FinanceBench marks as support.

No model, network, or API key is used after the dataset has been fetched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus, stable_id
from filingcopilot.eval_support import classify_support, require_file
from filingcopilot.models import Filing, Passage
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings

TICKER = "FB"
AS_OF = date(2026, 12, 31)
FILED = date(2024, 1, 1)
PERIOD_END = date(2023, 12, 31)
DEFAULT_DATA = Path("evaluation/external/data/financebench.jsonl")
ROOT = Path(__file__).resolve().parents[1]
MANAGED_HOME_MARKER = ".filingcopilot-eval-home"

FINANCE_SYNONYMS = {
    "capital expenditure": "capital expenditures capex purchases of property plant and equipment pp&e",
    "capex": "capital expenditures purchases of property plant and equipment pp&e",
    "revenue": "revenue net sales total revenue",
    "net sales": "net sales revenue total revenue",
    "operating income": "operating income income from operations operating profit",
    "net income": "net income net earnings",
    "cash flow from operations": "cash flow from operations net cash provided by operating activities",
    "gross margin": "gross margin gross profit",
    "accounts receivable": "accounts receivable trade receivables",
    "inventory": "inventory inventories",
    "debt": "debt borrowings",
    "shareholders equity": "shareholders equity stockholders equity",
    "earnings per share": "earnings per share eps diluted",
    "research and development": "research and development r&d",
    "lease": "lease leases rental",
    "income tax": "income tax taxes provision",
}


def expand_question(question: str) -> str:
    folded = question.casefold()
    extra = []
    for phrase, expansion in FINANCE_SYNONYMS.items():
        if phrase in folded:
            extra.extend(expansion.split())
    return " ".join(dict.fromkeys([question, *extra]))


def company_ticker(company: str) -> str:
    return "C" + hashlib.sha256(company.encode()).hexdigest()[:8].upper()


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


HEADING_RE = re.compile(
    r"^\s*(item\s+\d+[a-z]?|note\s+\d+|part\s+[ivx]+|"
    r"consolidated\s+(?:statements?|balance)|management'?s\s+discussion|"
    r"financial\s+statements|signatures|table\s+of\s+contents)\b",
    re.I,
)


def _fixed_chunks(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or len(text) <= size:
        return [text.strip()] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind(" ", start + int(size * 0.6), end)
            if cut > start:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    return paragraphs


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _merge_units(units: list[str], size: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        if not unit:
            continue
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= size:
            current += "\n" + unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _recursive_chunks(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text.strip()] if text.strip() else []
    units = []
    for paragraph in _split_paragraphs(text):
        if len(paragraph) > size:
            units.extend(_fixed_chunks(paragraph, size, overlap))
        else:
            units.append(paragraph)
    if len(units) <= 1:
        units = _split_sentences(text)
        expanded = []
        for sentence in units:
            if len(sentence) > size:
                expanded.extend(_fixed_chunks(sentence, size, overlap))
            else:
                expanded.append(sentence)
        units = expanded
    return _merge_units(units, size)


def _is_heading(line: str) -> bool:
    if len(line) > 120:
        return False
    if HEADING_RE.search(line):
        return True
    letters = [char for char in line if char.isalpha()]
    return (
        len(letters) >= 4
        and line.upper() == line
        and len(line.split()) <= 12
    )


def _is_table_line(line: str) -> bool:
    if len(line) > 300:
        return False
    numbers = re.findall(r"[-+]?\$?\d[\d,.]*%?", line)
    return len(numbers) >= 2 or ("|" in line and bool(numbers)) or ("\t" in line and len(numbers) >= 2)


def _split_sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _is_heading(line) and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def _section_chunks(text: str, size: int, overlap: int) -> list[str]:
    chunks = []
    for section in _split_sections(text):
        if len(section) <= size:
            chunks.append(section)
        else:
            chunks.extend(_recursive_chunks(section, size, overlap))
    return chunks


def _section_table_chunks(text: str, size: int, overlap: int) -> list[str]:
    chunks = []
    for section in _split_sections(text):
        if len(section) <= size:
            chunks.append(section)
        else:
            chunks.extend(_table_chunks(section, size, overlap))
    return chunks


def _table_chunks(text: str, size: int, overlap: int) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    blocks: list[tuple[str, list[str]]] = []
    current_type = ""
    current: list[str] = []
    for line in lines:
        line_type = "table" if _is_table_line(line) else "prose"
        if current_type and line_type != current_type:
            blocks.append((current_type, current))
            current = []
        current_type = line_type
        current.append(line)
    if current:
        blocks.append((current_type, current))

    chunks: list[str] = []
    for block_type, block in blocks:
        block_text = "\n".join(block).strip()
        if not block_text:
            continue
        if block_type == "prose":
            chunks.extend(_recursive_chunks(block_text, size, overlap))
            continue
        if len(block_text) <= size:
            chunks.append(block_text)
            continue
        header = block[:2]
        rows = block[2:] or block
        group: list[str] = []
        for row in rows:
            candidate = "\n".join([*header, *group, row])
            if group and len(candidate) > size:
                chunks.append("\n".join([*header, *group]))
                group = [row]
            else:
                group.append(row)
        if group:
            chunks.append("\n".join([*header, *group]))
    return [chunk for chunk in chunks if chunk.strip()]


def chunk_page(
    text: str,
    strategy: str,
    size: int,
    overlap: int,
    *,
    company: str,
    form: str,
    page: int,
) -> list[str]:
    if strategy == "fixed":
        return _fixed_chunks(text, size, overlap)
    if strategy == "sentence":
        return _merge_units(_split_sentences(text), size)
    if strategy == "section":
        return _section_chunks(text, size, overlap)
    if strategy == "section_table":
        return _section_table_chunks(text, size, overlap)
    if strategy == "table":
        return _table_chunks(text, size, overlap)
    if strategy == "contextual":
        prefix = f"{company} | {form} | page {page}\n"
        return [prefix + chunk for chunk in _recursive_chunks(text, size, overlap)]
    # recursive and child
    return _recursive_chunks(text, size, overlap)


def prepare_home(home: Path, *, force: bool = False) -> Path:
    """Create a disposable evaluation home without deleting unmanaged data.

    The evaluator rebuilds its corpus, so an existing managed home may be
    replaced. Any other nonempty directory requires an explicit ``--force``;
    the repository root, the current directory and the user's home are always
    rejected.
    """
    requested = home.expanduser()
    if requested.is_symlink():
        raise ValueError(f"Evaluation home must not be a symlink: {requested}")
    home = requested.resolve()
    protected = {
        Path(home.anchor).resolve(),
        Path.home().resolve(),
        ROOT.resolve(),
        Path.cwd().resolve(),
    }
    if home in protected:
        raise ValueError(f"Refusing to use protected directory as evaluation home: {home}")
    if home.exists() and not home.is_dir():
        raise ValueError(f"Evaluation home is not a directory: {home}")
    if home.exists():
        has_contents = any(home.iterdir())
        if has_contents and not (home / MANAGED_HOME_MARKER).is_file() and not force:
            raise ValueError(
                f"Refusing to delete unmanaged directory {home}; "
                "use a new/empty directory or pass --force explicitly"
            )
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / MANAGED_HOME_MARKER).write_text(
        "Managed by scripts/evaluate_financebench.py; safe to rebuild.\n",
        encoding="utf-8",
    )
    return home


def build_corpus(
    rows: list[dict],
    home: Path,
    *,
    chunk_size: int = 0,
    chunk_overlap: int = 120,
    chunk_strategy: str = "recursive",
    force: bool = False,
) -> dict:
    prepare_home(home, force=force)
    corpus = Corpus(home)
    pages: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        for evidence in row.get("evidence", []):
            doc = evidence.get("doc_name") or row.get("doc_name")
            page = int(evidence.get("evidence_page_num") or 0)
            text = evidence.get("evidence_text_full_page") or evidence.get("evidence_text") or ""
            if doc and page and text:
                pages[doc].setdefault(page, {"text": text, "row": row})
    passages_total = 0
    chars_total = 0
    for doc, page_map in pages.items():
        row = next(iter(page_map.values()))["row"]
        ticker = company_ticker(row.get("company") or doc)
        period_end = date(int(row.get("doc_period") or 2023), 12, 31)
        filing = Filing(
            id=doc,
            ticker=ticker,
            company=row.get("company") or doc,
            cik="0000000000",
            accession=doc,
            form=row.get("doc_type") or "filing",
            filed=date(period_end.year + 1, 1, 1),
            period_end=period_end,
            source_url=row.get("doc_link") or "",
            content_sha256=hashlib.sha256(doc.encode()).hexdigest(),
        )
        passages = []
        for page, payload in sorted(page_map.items()):
            chunks = chunk_page(
                payload["text"],
                chunk_strategy,
                chunk_size,
                chunk_overlap,
                company=filing.company,
                form=filing.form,
                page=page,
            )
            for index, chunk in enumerate(chunks):
                passages.append(
                    Passage(
                        id=stable_id("passage", doc, page, index, chunk[:80]),
                        filing_id=doc,
                        ticker=ticker,
                        section=f"{filing.company} | {filing.form} | page {page}",
                        text=chunk,
                        source_url=filing.source_url,
                        locator=f"page {page} chunk {index}",
                    )
                )
                chars_total += len(chunk)
        corpus.ingest(filing, [], passages)
        passages_total += len(passages)
    return {
        "pages": sum(len(page_map) for page_map in pages.values()),
        "passages": passages_total,
        "avg_chunk_chars": round(chars_total / passages_total, 1) if passages_total else 0,
    }


def page_hit(hit: dict, gold_pages: set[tuple[str, int]]) -> bool:
    passage = hit["passage"]
    page_match = re.search(r"\d+", passage.get("locator", ""))
    page = int(page_match.group()) if page_match else None
    return (passage.get("filing_id"), page) in gold_pages


def expand_hit(hit: dict, page_texts: dict[tuple[str, int], str], parent_chars: int) -> str:
    passage = hit["passage"]
    locator = passage.get("locator", "")
    page_match = re.search(r"page\s+(\d+)", locator)
    page = int(page_match.group(1)) if page_match else None
    page_text = page_texts.get((passage.get("filing_id"), page)) if page is not None else None
    if not page_text:
        return passage.get("text", "")
    chunk = passage.get("text", "")
    index = page_text.find(chunk)
    if index < 0:
        index = page_text.find(chunk[:200])
    if index < 0:
        return chunk
    start = max(0, index - parent_chars // 4)
    end = min(len(page_text), start + parent_chars)
    return page_text[start:end]


def compact_match(text: str, evidence: str) -> bool:
    compact_text = re.sub(r"[^a-z0-9]+", "", norm(text))
    compact_evidence = re.sub(r"[^a-z0-9]+", "", norm(evidence))
    if not compact_evidence or not compact_text:
        return False
    if len(compact_evidence) >= 40 and compact_evidence in compact_text:
        return True
    prefix = compact_evidence[:400]
    return len(prefix) >= 40 and prefix in compact_text


def snippet_hit(hit: dict, snippets: list[str], page_texts: dict[tuple[str, int], str], parent_chars: int) -> bool:
    text = norm(expand_hit(hit, page_texts, parent_chars))
    for snippet in snippets:
        if not snippet:
            continue
        if norm(snippet) in text:
            return True
        if compact_match(text, snippet):
            return True
        wanted = tokens(snippet)
        if wanted and len(wanted & tokens(text)) / len(wanted) >= 0.90:
            return True
    return False


def answer_support_report(texts: list[str], answer: object) -> dict:
    return classify_support(texts, answer).as_dict()


def answer_supported_text(text: str, answer: str) -> bool:
    """Compatibility helper: full support against one evidence text."""
    return answer_support_report([text], answer)["status"] == "full"


def answer_support_page(hit: dict, answer: str, page_texts: dict[tuple[str, int], str]) -> bool:
    passage = hit["passage"]
    locator = passage.get("locator", "")
    page_match = re.search(r"page\s+(\d+)", locator)
    page = int(page_match.group(1)) if page_match else None
    text = page_texts.get((passage.get("filing_id"), page), "") if page is not None else ""
    return answer_supported_text(text, answer)


def answer_support(hit: dict, answer: str, page_texts: dict[tuple[str, int], str], parent_chars: int) -> bool:
    return answer_supported_text(expand_hit(hit, page_texts, parent_chars), answer)


def numeric_density(text: str) -> int:
    return len(re.findall(r"\d", text))


def is_numeric_question(question: str) -> bool:
    folded = question.casefold()
    if re.search(r"\d", question):
        return True
    terms = (
        "how much",
        "how many",
        "what was",
        "what is",
        "ratio",
        "percent",
        "margin",
        "average",
        "increase",
        "decrease",
        "highest",
        "lowest",
        "most",
        "least",
    )
    return any(term in folded for term in terms)


def title_match_score(hit: dict, question: str, page_texts: dict[tuple[str, int], str], parent_chars: int) -> int:
    text = norm(expand_hit(hit, page_texts, parent_chars))[:400]
    stop = frozenset(
        "what is the a an of for to from with about which how does did do was were are be been being "
        "and or in on by as at this that these those it its company fy fy20".split()
    )
    query_terms = {
        term
        for term in re.findall(r"[a-z0-9]+", question.casefold())
        if term not in stop
    }
    head_terms = set(re.findall(r"[a-z0-9]+", text))
    return len(query_terms & head_terms)


def rerank_top1(
    hits: list[dict],
    question: str,
    page_texts: dict[tuple[str, int], str],
    parent_chars: int,
    mode: str,
) -> list[dict]:
    if mode == "off" or len(hits) < 2:
        return hits
    limit = min(3, len(hits))
    if mode == "digits":
        if not is_numeric_question(question):
            return hits
        scores = [
            numeric_density(expand_hit(hit, page_texts, parent_chars))
            for hit in hits[:limit]
        ]
        candidate = max(range(limit), key=lambda index: (scores[index], -index))
        if candidate == 0:
            return hits
        if scores[candidate] < max(2 * scores[0], scores[0] + 30):
            return hits
    elif mode == "title":
        scores = [
            title_match_score(hit, question, page_texts, parent_chars)
            for hit in hits[:limit]
        ]
        candidate = max(range(limit), key=lambda index: (scores[index], -index))
        if candidate == 0 or scores[candidate] <= scores[0]:
            return hits
    else:
        return hits
    reordered = list(hits)
    reordered[0], reordered[candidate] = reordered[candidate], reordered[0]
    return reordered


def rerank_flashrank(
    hits: list[dict], question: str, ranker: object
) -> list[dict]:
    if not hits:
        return hits
    passages = [
        {"id": index, "text": hit["passage"].get("text", "")[:2000]}
        for index, hit in enumerate(hits)
    ]
    from flashrank import RerankRequest

    ranked = ranker.rerank(RerankRequest(query=question, passages=passages))
    order = [item["id"] for item in ranked]
    return [hits[index] for index in order if index < len(hits)]


def evaluate(
    rows: list[dict],
    retriever: Retriever,
    k: int = 5,
    scope: str = "global",
    parent_chars: int = 2000,
    finance_expand: bool = False,
    top1_rerank_mode: str = "title",
    ranker: object | None = None,
) -> dict:
    page_texts: dict[tuple[str, int], str] = {}
    for row in rows:
        for evidence in row.get("evidence", []):
            doc = evidence.get("doc_name") or row.get("doc_name")
            page = int(evidence.get("evidence_page_num") or 0)
            text = evidence.get("evidence_text_full_page") or evidence.get("evidence_text") or ""
            if doc and page and text:
                page_texts[(doc, page)] = text
    results = []
    for row in rows:
        gold_pages = {
            (e.get("doc_name") or row.get("doc_name"), int(e.get("evidence_page_num") or 0))
            for e in row.get("evidence", [])
        }
        snippets = [
            e.get("evidence_text") or e.get("evidence_text_full_page") or ""
            for e in row.get("evidence", [])
        ]
        ticker = (
            None
            if scope == "global"
            else company_ticker(row.get("company") or row.get("doc_name") or "")
        )
        fiscal_year = (
            int(row["doc_period"])
            if scope in {"company_year", "company_doc"} and row.get("doc_period")
            else None
        )
        filing_id = row.get("doc_name") if scope == "company_doc" else None
        started = time.perf_counter()
        question = row["question"]
        search_query = expand_question(question) if finance_expand else question
        result = retriever.search(
            search_query,
            AS_OF,
            ticker=ticker,
            k=k,
            fiscal_year=fiscal_year,
            filing_id=filing_id,
        )
        elapsed = (time.perf_counter() - started) * 1000
        hits = result["hits"][:k]
        if ranker is not None:
            rerank_started = time.perf_counter()
            hits = rerank_flashrank(hits, question, ranker)
            elapsed += (time.perf_counter() - rerank_started) * 1000
        hits = rerank_top1(hits, question, page_texts, parent_chars, top1_rerank_mode)
        page_ranks = [rank for rank, hit in enumerate(hits, 1) if page_hit(hit, gold_pages)]
        snippet_ranks = [
            rank
            for rank, hit in enumerate(hits, 1)
            if snippet_hit(hit, snippets, page_texts, parent_chars)
        ]
        answer_text = row.get("answer", "")
        expanded_hits = [
            expand_hit(hit, page_texts, parent_chars) for hit in hits
        ]
        page_hit_texts = []
        for hit in hits:
            passage = hit["passage"]
            match = re.search(r"page\s+(\d+)", passage.get("locator", ""))
            page_hit_texts.append(
                page_texts.get((passage.get("filing_id"), int(match.group(1))), "")
                if match
                else ""
            )
        top1_report = answer_support_report(
            [expanded_hits[0]] if expanded_hits else [], answer_text
        )
        at5_report = answer_support_report(expanded_hits, answer_text)
        top1_page_report = answer_support_report(
            [page_hit_texts[0]] if page_hit_texts else [], answer_text
        )
        at5_page_report = answer_support_report(page_hit_texts, answer_text)
        gold_texts = [page_texts.get(pair, "") for pair in gold_pages] + snippets
        evaluable_report = answer_support_report(gold_texts, answer_text)
        answer_support_ranks = [
            rank
            for rank, hit in enumerate(hits, 1)
            if answer_support(hit, answer_text, page_texts, parent_chars)
        ]
        results.append(
            {
                "id": row["financebench_id"],
                "company": row.get("company"),
                "doc_name": row.get("doc_name"),
                "doc_type": row.get("doc_type"),
                "question_type": row.get("question_type"),
                "question": row["question"],
                "answer": row.get("answer"),
                "answer_evaluable": evaluable_report["status"] == "full",
                "answer_evaluable_status": evaluable_report["status"],
                "page_hit": bool(page_ranks),
                "snippet_hit": bool(snippet_ranks),
                "page_rank": page_ranks[0] if page_ranks else None,
                "snippet_rank": snippet_ranks[0] if snippet_ranks else None,
                "answer_supported_top1": top1_report["status"] == "full",
                "answer_partial_top1": top1_report["status"] == "partial",
                "answer_unsupported_top1": top1_report["status"] == "unsupported",
                "answer_support_top1": top1_report,
                "answer_supported_at_5": at5_report["status"] == "full",
                "answer_partial_at_5": at5_report["status"] == "partial",
                "answer_unsupported_at_5": at5_report["status"] == "unsupported",
                "answer_support_at_5": at5_report,
                "answer_support_rank": answer_support_ranks[0] if answer_support_ranks else None,
                "answer_supported_top1_page": top1_page_report["status"] == "full",
                "answer_partial_top1_page": top1_page_report["status"] == "partial",
                "answer_supported_at_5_page": at5_page_report["status"] == "full",
                "answer_partial_at_5_page": at5_page_report["status"] == "partial",
                "answer_support_top1_page": top1_page_report,
                "answer_support_at_5_page": at5_page_report,
                "elapsed_ms": elapsed,
            }
        )
    n = len(results) or 1
    durations = sorted(row["elapsed_ms"] for row in results)
    metrics = {
        "questions": len(results),
        "page_hit_at_1": sum(1 for row in results if row["page_rank"] == 1) / n,
        "page_hit_at_3": sum(1 for row in results if row["page_rank"] and row["page_rank"] <= 3) / n,
        "page_hit_at_5": sum(row["page_hit"] for row in results) / n,
        "snippet_hit_at_1": sum(1 for row in results if row["snippet_rank"] == 1) / n,
        "snippet_hit_at_3": sum(1 for row in results if row["snippet_rank"] and row["snippet_rank"] <= 3) / n,
        "snippet_hit_at_5": sum(row["snippet_hit"] for row in results) / n,
        "mrr_at_5": sum(1 / row["page_rank"] for row in results if row["page_rank"]) / n,
        "answer_supported_top1": sum(row["answer_supported_top1"] for row in results) / n,
        "answer_partial_top1": sum(row["answer_partial_top1"] for row in results) / n,
        "answer_unsupported_top1": sum(row["answer_unsupported_top1"] for row in results) / n,
        "answer_supported_at_5": sum(row["answer_supported_at_5"] for row in results) / n,
        "answer_partial_at_5": sum(row["answer_partial_at_5"] for row in results) / n,
        "answer_unsupported_at_5": sum(row["answer_unsupported_at_5"] for row in results) / n,
        "answer_supported_top1_page": sum(row["answer_supported_top1_page"] for row in results) / n,
        "answer_partial_top1_page": sum(row["answer_partial_top1_page"] for row in results) / n,
        "answer_supported_at_5_page": sum(row["answer_supported_at_5_page"] for row in results) / n,
        "answer_partial_at_5_page": sum(row["answer_partial_at_5_page"] for row in results) / n,
        "answer_evaluable_questions": sum(
            1 for row in results if row["answer_evaluable"]
        ),
        "answer_partial_evaluable_questions": sum(
            1 for row in results if row["answer_evaluable_status"] == "partial"
        ),
        "answer_unsupported_evaluable_questions": sum(
            1 for row in results if row["answer_evaluable_status"] == "unsupported"
        ),
        "answer_supported_top1_evaluable": sum(
            row["answer_supported_top1"] for row in results if row["answer_evaluable"]
        )
        / (sum(1 for row in results if row["answer_evaluable"]) or 1),
        "answer_supported_at_5_evaluable": sum(
            row["answer_supported_at_5"] for row in results if row["answer_evaluable"]
        )
        / (sum(1 for row in results if row["answer_evaluable"]) or 1),
        "answer_supported_top1_page_evaluable": sum(
            row["answer_supported_top1_page"]
            for row in results
            if row["answer_evaluable"]
        )
        / (sum(1 for row in results if row["answer_evaluable"]) or 1),
        "p50_ms": durations[len(durations) // 2] if durations else 0,
        "p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0,
    }
    for key in ("question_type", "doc_type"):
        grouped = defaultdict(list)
        for row in results:
            grouped[row[key]].append(row)
        metrics[f"by_{key}"] = {
            name: {
                "questions": len(group),
                "page_hit_at_5": sum(r["page_hit"] for r in group) / len(group),
                "snippet_hit_at_5": sum(r["snippet_hit"] for r in group) / len(group),
            }
            for name, group in sorted(grouped.items())
        }
    return {"metrics": metrics, "rows": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--home", type=Path, default=Path(".financebench-eval"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow rebuilding a nonempty unmanaged home directory. Never allowed for protected roots.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--mode", choices=("coverage", "anchor", "rrf"), default="coverage")
    parser.add_argument(
        "--scope",
        choices=("global", "company", "company_year", "company_doc"),
        default="company_year",
        help="Product-style scope. company_year matches the normal selected-company/as-of use.",
    )
    parser.add_argument(
        "--chunk-strategy",
        choices=("fixed", "recursive", "section", "section_table", "table", "sentence", "child", "contextual"),
        default="recursive",
        help="Chunking strategy; fixed is the old hard-cut baseline.",
    )
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument(
        "--parent-chars",
        type=int,
        default=2000,
        help="Expand a retrieved chunk to this many source characters for snippet/answer checks.",
    )
    parser.add_argument(
        "--top1-rerank",
        choices=("off", "digits", "title"),
        default="title",
        help="Top1 rerank mode. 'title' promotes a top-3 page whose heading matches more query terms.",
    )
    parser.add_argument(
        "--rerank", choices=("off", "flashrank"), default="off"
    )
    parser.add_argument("--embedding-mode", choices=("off", "local"), default="off")
    parser.add_argument(
        "--embedding-model", default="BAAI/bge-small-en-v1.5"
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/financebench-eval.json"))
    args = parser.parse_args()

    require_file(args.data, "FinanceBench JSONL")
    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = rows[: args.limit] if args.limit else rows
    corpus_info = build_corpus(
        rows,
        args.home,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        chunk_strategy=args.chunk_strategy,
        force=args.force,
    )
    settings = Settings(
        home=args.home,
        embedding_mode=args.embedding_mode,
        embedding_model=args.embedding_model,
        rerank_mode=args.mode,
    )
    retriever = Retriever(Corpus(args.home), settings)
    ranker = None
    if args.rerank == "flashrank":
        from flashrank import Ranker

        ranker = Ranker(
            model_name="ms-marco-MiniLM-L-12-v2",
            cache_dir=str(Path("models") / "flashrank"),
        )
    report = evaluate(
        rows,
        retriever,
        args.k,
        args.scope,
        args.parent_chars,
        top1_rerank_mode=args.top1_rerank,
        ranker=ranker,
    )
    payload = {
        "schema": "filingcopilot.financebench-eval/v1",
        "dataset": "PatronusAI/financebench",
        "license": "CC-BY-NC-4.0",
        "scope": args.scope,
        "mode": args.mode,
        "embedding_mode": args.embedding_mode,
        "embedding_model": args.embedding_model,
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
        "chunk_strategy": args.chunk_strategy,
        "parent_chars": args.parent_chars,
        "top1_rerank": args.top1_rerank,
        "rerank": args.rerank,
        "support_definition": (
            "full requires every key number, unit and period in one evidence set; "
            "verified calculation inputs may support a result that is not copied "
            "verbatim; partial means some required values are present; unsupported "
            "means no required number is covered"
        ),
        "avg_chunk_chars": corpus_info["avg_chunk_chars"],
        "corpus_pages": corpus_info["pages"],
        "corpus_passages": corpus_info["passages"],
        **report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
