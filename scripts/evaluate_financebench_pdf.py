"""Full-filing FinanceBench retrieval check on real 10-K/10-Q PDFs.

This downloads a small number of filing PDFs, extracts page text with pypdf, and
retrieves the evidence page/snippet from the *full document*. It is the honest
counterpart to the evidence-page-only benchmark.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

import httpx
import numpy as np
from fastembed import TextEmbedding
from pypdf import PdfReader

from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.retrieval import _coverage_text, _matches_coverage_term, rewrite_query
from filingcopilot.retrieval_query import lexical_terms, retrieval_text

ROOT = Path(__file__).resolve().parents[1]
FB_PATH = ROOT / "scripts" / "evaluate_financebench.py"
DATA = ROOT / "evaluation" / "external" / "data" / "financebench.jsonl"
PDF_DIR = ROOT / "evaluation" / "external" / "data" / "financebench_pdfs"


def _load_chunk_page():
    spec = importlib.util.spec_from_file_location("financebench_eval", FB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.chunk_page


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def direct_pdf_url(link: str) -> str:
    if "pdfTarget=" in link:
        target = link.split("pdfTarget=", 1)[1]
        target = unquote(target)
        try:
            return base64.b64decode(target).decode("utf-8")
        except Exception:
            return link
    return link


def download_pdf(url: str, path: Path) -> Path:
    if path.exists() and path.stat().st_size > 1000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 FilingCopilot evaluation"}
    with httpx.Client(timeout=120.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path


def page_texts(pdf_path: Path, extractor: str = "pypdf") -> list[str]:
    if extractor in {"pymupdf", "hybrid"}:
        try:
            import fitz

            with fitz.open(str(pdf_path)) as document:
                mupdf_pages = [
                    page.get_text("text", sort=True) or "" for page in document
                ]
        except Exception:
            mupdf_pages = []
        else:
            if extractor == "pymupdf":
                return mupdf_pages
    else:
        mupdf_pages = []
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    if extractor == "hybrid" and mupdf_pages:
        hybrid_pages = []
        for index, page in enumerate(pages):
            other = mupdf_pages[index] if index < len(mupdf_pages) else ""
            hybrid_pages.append("\n".join(dict.fromkeys(part for part in (page, other) if part)))
        return hybrid_pages
    return pages


def find_evidence_page(pages: list[str], evidence: dict, fallback: int) -> int | None:
    needle = norm(evidence.get("evidence_text", ""))[:200]
    if needle:
        for index, page in enumerate(pages):
            if needle in norm(page):
                return index
    if 0 <= fallback < len(pages):
        return fallback
    return None


def snippet_match(text: str, evidence: str) -> bool:
    text = norm(text)
    evidence = norm(evidence)
    if not evidence:
        return False
    if evidence in text:
        return True
    wanted = set(re.findall(r"[a-z0-9]+", evidence))
    if not wanted:
        return False
    return len(wanted & set(re.findall(r"[a-z0-9]+", text))) / len(wanted) >= 0.90


def expand_parent(page: str, chunk: str, parent_chars: int) -> str:
    index = page.find(chunk)
    if index < 0:
        index = page.find(chunk[:200])
    if index < 0:
        return chunk
    start = max(0, index - parent_chars // 4)
    end = min(len(page), start + parent_chars)
    return page[start:end]


FINANCE_SYNONYMS = {
    "legal battles": "legal proceedings litigation lawsuits contingencies",
    "legal battle": "legal proceedings litigation lawsuits contingencies",
    "legal proceedings": "legal proceedings litigation lawsuits",
    "capital expenditure": "capital expenditures capex purchases of property plant and equipment pp&e",
    "capex": "capital expenditures purchases of property plant and equipment pp&e",
    "revenue": "revenue net sales total revenue",
    "net sales": "net sales revenue total revenue",
    "operating income": "operating income income from operations operating profit",
    "net income": "net income net earnings",
    "cash flow from operations": "cash flow from operations net cash provided by operating activities",
    "cash flow": "cash flow cash flows",
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


INTENT_PHRASES = {
    "cash_flow": (
        "statement of cash flow",
        "statements of cash flow",
        "cash flows from operating",
        "operating activities",
        "investing activities",
        "financing activities",
    ),
    "balance_sheet": (
        "balance sheet",
        "total assets",
        "total liabilities",
        "shareholders' equity",
        "stockholders' equity",
    ),
    "income": (
        "statement of income",
        "statements of income",
        "net sales",
        "operating income",
        "gross profit",
        "cost of sales",
    ),
    "segment": ("business segment", "business segments", "organic sales", "segment sales"),
    "mda": ("management's discussion", "results of operations", "sg&a"),
}

TITLE_PHRASES = {
    "cash_flow": (
        "consolidated statement of cash flow",
        "consolidated statements of cash flow",
    ),
    "balance_sheet": (
        "consolidated balance sheet",
        "consolidated balance sheets",
        "consolidated statement of financial position",
        "consolidated statements of financial position",
    ),
    "income": (
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated statement of operations",
        "consolidated statements of operations",
        "consolidated statement of earnings",
        "consolidated statements of earnings",
    ),
    "segment": (
        "sales and operating income",
        "sales and operating income (loss) by business segment",
        "segment revenues",
        "segment operating income",
        "reportable segments",
        "segment information",
        "segment results",
    ),
    "mda": (
        "results of operations",
        "management's discussion",
        "management's discussion and analysis",
    ),
}

TOPIC_ROUTES = {
    "business": (
        "item 1. business",
        "business overview",
        "company overview",
        "our business",
        "who we are",
    ),
    "risk": ("item 1a. risk factors", "risk factors"),
    "mda": (
        "item 7. management",
        "management's discussion",
        "results of operations",
        "card member",
    ),
    "tax": (
        "income taxes",
        "effective tax rate",
        "summary of financial performance",
        "selected income statement data",
        "consolidated statements of operations",
        "consolidated statement of operations",
        "consolidated statements of income",
        "consolidated statement of income",
        "results of operations",
    ),
    "earnings": (
        "non-gaap",
        "non gaap",
        "ebitda",
        "financial results",
        "earnings release",
        "components of",
        "net sales growth",
    ),
    "legal": ("legal proceedings", "litigation", "contingencies"),
    "geographic": (
        "geographic information",
        "geographic areas",
        "geographic regions",
    ),
    "segment": (
        "segment information",
        "business segments",
        "reportable segments",
        "segment results",
        "summary of business segment",
        "segment data",
    ),
    "acquisition": ("acquisitions", "business acquisitions", "acquisition"),
    "cash_conversion": (
        "consolidated statements of earnings",
        "consolidated statements of operations",
        "consolidated statements of income",
    ),
    "capital": (
        "selected capital-related metrics",
        "capital-related metrics",
        "capital adequacy",
        "capital ratios",
        "book value per share",
    ),
}

TOPIC_BODY_TERMS = {
    "business": ("cyclical", "customer accounted"),
    "risk": ("customer concentration", "customer accounted"),
    "mda": ("card member",),
    "tax": ("effective tax rate", "income tax", "provision for income tax"),
    "earnings": ("net sales", "constant currency", "non-gaap", "ebitda"),
    "legal": ("legal proceeding", "litigation", "contingenc", "lawsuit"),
    "geographic": ("geographic",),
    "segment": ("summary of business segment", "segment data", "reportable segment"),
    "acquisition": ("acquisition", "acquired"),
    "cash_conversion": (
        "cost of sales",
        "net sales",
        "average inventory",
        "accounts receivable",
        "accounts payable",
    ),
    "capital": ("book value per share", "cet1", "capital ratio", "tangible book value"),
}


def question_topics(question: str) -> tuple[set[str], bool]:
    folded = question.casefold()
    topics: set[str] = set()
    explicit = False
    if any(term in folded for term in ("industry", "products", "services", "geographies", "operates", "card members", "cyclical", "customers")):
        topics.add("business")
        explicit = True
    if any(term in folded for term in ("acquisition", "acquired")):
        topics.add("acquisition")
        explicit = True
    if any(term in folded for term in ("cash conversion cycle", "ccc", "dio", "dso")):
        topics.add("cash_conversion")
        explicit = True
    if any(term in folded for term in ("bankrupt", "liquidation", "liquidated", "book value", "capital ratio", "cet1")):
        topics.add("capital")
        explicit = True
    if "customer concentration" in folded:
        topics.add("risk")
        explicit = True
    if any(term in folded for term in ("legal battle", "legal battles", "legal proceeding", "litigation", "lawsuit", "contingenc")):
        topics.add("legal")
        explicit = True
    if any(term in folded for term in ("geograph", "geographic")):
        topics.add("geographic")
        explicit = True
    if any(term in folded for term in ("product categories", "service categories", "product category")):
        topics.add("segment")
        explicit = True
    if any(term in folded for term in ("revenue change", "drove revenue", "sales change", "retention", "retain")):
        topics.add("mda")
        explicit = True
    if any(term in folded for term in ("effective tax rate", "tax rate")):
        topics.add("tax")
        explicit = True
    if any(term in folded for term in ("adjusted ebitda", "adjusted non gaap", "non-gaap", "non gaap")):
        topics.add("earnings")
        explicit = True
    return topics, explicit


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm(value))


def numeric_density(value: str) -> int:
    return len(re.findall(r"\d", value[:4000]))


def question_intents(question: str) -> tuple[set[str], bool]:
    folded = question.casefold()
    intents: set[str] = set()
    explicit = False
    if any(term in folded for term in ("balance sheet", "statement of financial position")):
        intents.add("balance_sheet")
        explicit = True
    if any(term in folded for term in ("days payable outstanding", "dpo")):
        intents.update({"balance_sheet", "income"})
        explicit = True
    elif any(term in folded for term in ("quick ratio", "current ratio", "liquidity", "working capital", "accounts payable")):
        intents.add("balance_sheet")
        explicit = True
    if any(term in folded for term in ("cash flow statement", "statement of cash flows", "cash flows from operating", "free cash flow", "free cashflow", "cash conversion")):
        intents.add("cash_flow")
        explicit = True
    if any(term in folded for term in ("income statement", "statement of income", "statement of operations", "statement of earnings")):
        intents.add("income")
        explicit = True
    if any(term in folded for term in ("business segment", "segment sales", "organic sales", "segment")):
        intents.add("segment")
        explicit = True
    if any(term in folded for term in ("capital expenditure", "capex", "cash flow", "operating activities")):
        intents.add("cash_flow")
        explicit = True
    if any(term in folded for term in ("total assets", "liabilities", "equity", "receivable", "inventory")):
        intents.add("balance_sheet")
    if any(term in folded for term in ("capital-intensive", "operating margin", "gross margin", "revenue", "net sales", "operating income", "sg&a", "net income")):
        intents.add("income")
    if "capital-intensive" in folded:
        explicit = True
    if "operating margin" in folded or "sg&a" in folded or "management" in folded or "results of operations" in folded:
        intents.add("mda")
        explicit = True
    return intents, explicit


def page_intent_labels(text: str) -> set[str]:
    head = norm(text)[:1800]
    labels = set()
    for label, phrases in INTENT_PHRASES.items():
        if any(phrase in head for phrase in phrases):
            labels.add(label)
    return labels


SECTION_END_MARKERS = {
    "business": ("item1a", "item1b", "item2"),
    "risk": ("item2", "item7"),
    "mda": ("item7a", "item8"),
    "segment": ("item8",),
}
SECTION_MAX_SPAN = {
    "business": 50,
    "risk": 50,
    "mda": 40,
    "legal": 12,
    "tax": 12,
    "earnings": 12,
    "geographic": 15,
    "segment": 20,
}

INDEX_PAGE_MARKERS = (
    "indextotheform10k",
    "indextotheconsolidatedfinancialstatements",
    "indextofinancialstatements",
)

PAGE_BONUSES = {
    "managed basis": 6.0,
    "business segment results": 3.0,
    "segment results": 2.0,
}

TOPIC_BONUS = {
    "cash_conversion": 10.0,
    "capital": 6.0,
}


def topic_section_pages(pages: list[str], topics: set[str]) -> set[int]:
    if not topics:
        return set()
    heads = [compact(page)[:500] for page in pages]
    selected = set()
    for topic in topics:
        phrases = [compact(phrase) for phrase in TOPIC_ROUTES[topic]]
        start = None
        for index, head in enumerate(heads):
            if index < 2:
                continue
            if any(phrase in head for phrase in phrases):
                start = index
                break
        if start is None:
            continue
        end = min(len(pages), start + SECTION_MAX_SPAN.get(topic, 15))
        markers = SECTION_END_MARKERS.get(topic, ())
        for index in range(start + 1, end):
            if any(marker in heads[index] for marker in markers):
                end = index
                break
        selected.update(range(start, max(start + 1, end)))
    return selected


def bm25_page_scores(pages: list[str], terms: list[str], question: str) -> dict[int, tuple[int, float]]:
    tokenized = [re.findall(r"[a-z0-9]+", norm(page)) for page in pages]
    total = len(tokenized)
    avg_length = sum(len(tokens) for tokens in tokenized) / max(1, total)
    df = {
        term: sum(1 for tokens in tokenized if term in tokens)
        for term in terms
    }
    idf = {
        term: math.log((total - df[term] + 0.5) / (df[term] + 0.5) + 1)
        for term in terms
    }
    intents, explicit = question_intents(question)
    topics, topic_explicit = question_topics(question)
    labels_by_page = [page_intent_labels(page) for page in pages]
    statement_heads = [compact(page)[:240] for page in pages]
    wide_statement_heads = [compact(page)[:500] for page in pages]
    topic_heads = [compact(page)[:500] for page in pages]
    folded_question = question.casefold()
    wide_topics = {"mda", "tax", "earnings", "legal", "geographic", "segment"}
    if "industry" in folded_question:
        wide_topics.add("business")
    title_pages = {
        index
        for index in range(len(pages))
        if intents
        and any(
            compact(phrase)
            in (wide_statement_heads[index] if label == "segment" else statement_heads[index])
            for label in intents
            for phrase in TITLE_PHRASES[label]
        )
    }
    topic_title_pages = {
        index
        for index in range(len(pages))
        if topics
        and any(
            compact(phrase)
            in (topic_heads[index] if topic in wide_topics else statement_heads[index])
            for topic in topics
            for phrase in TOPIC_ROUTES[topic]
        )
    }
    topic_body_pages = set()
    for index, page in enumerate(pages):
        folded_page = norm(page)
        for topic in topics:
            for phrase in TOPIC_BODY_TERMS.get(topic, ()):
                if phrase not in folded_page:
                    continue
                if phrase == "income tax" and numeric_density(page) < 40:
                    continue
                topic_body_pages.add(index)
    topic_pages = topic_title_pages | topic_body_pages
    allowed = set(range(len(pages)))
    if explicit and title_pages:
        allowed = title_pages
    elif topic_explicit and topic_pages:
        allowed = topic_pages
    elif explicit and intents:
        filtered = {index for index, labels in enumerate(labels_by_page) if labels & intents}
        if filtered:
            allowed = filtered
    scores = {}
    for page, tokens in enumerate(tokenized):
        if page not in allowed:
            continue
        length = len(tokens) or 1
        tf = {term: tokens.count(term) for term in terms}
        score = sum(
            idf[term] * tf[term] * 2.2 / (tf[term] + 1.2 * (1 - 0.75 + 0.75 * length / avg_length))
            for term in terms
            if tf[term]
        )
        if intents and labels_by_page[page] & intents:
            score += 4.0
        if page in title_pages:
            score += 8.0
            if numeric_density(pages[page]) >= 40:
                score += 5.0
        if page in topic_title_pages:
            score += 8.0
            score += max((TOPIC_BONUS.get(topic, 0.0) for topic in topics), default=0.0)
            if numeric_density(pages[page]) >= 40:
                score += 3.0
        if page in topic_body_pages:
            score += 3.0
        page_head = compact(pages[page])[:300]
        for phrase, bonus in PAGE_BONUSES.items():
            if compact(phrase) in page_head:
                score += bonus
        if any(marker in page_head for marker in INDEX_PAGE_MARKERS):
            score -= 8.0
        scores[page] = (0, score)
    return scores


def query_terms(question: str) -> list[str]:
    rewrite = rewrite_query(question)
    focused = retrieval_text(rewrite["expanded"], DEFAULT_DOMAIN, None, None)
    extra = []
    folded = question.casefold()
    for phrase, expansion in FINANCE_SYNONYMS.items():
        if phrase in folded:
            extra.extend(expansion.split())
    stop = frozenset(
        "what is the a an of for to from with about which how does did do was were are be been being "
        "give response question relying details shown according review please explain show me "
        "and or in on by as at this that these those it its".split()
    )
    terms = lexical_terms(focused) + extra
    return list(dict.fromkeys(term for term in terms if term.casefold() not in stop))


def page_dense_scores(
    pages: list[str], vectors: np.ndarray, query_vector: np.ndarray
) -> dict[int, tuple[int, float]]:
    denominator = np.linalg.norm(vectors, axis=1) * np.linalg.norm(query_vector)
    similarity = vectors @ query_vector / np.maximum(denominator, 1e-12)
    return {
        page: (0, float(similarity[page]))
        for page in range(min(len(pages), len(similarity)))
    }


def lexical_page_scores(
    chunks: list[tuple[int, str]], terms: list[str]
) -> dict[int, tuple[int, float]]:
    texts = [_coverage_text(text) for _, text in chunks]
    total = len(texts)
    df = {
        term: sum(1 for text in texts if _matches_coverage_term(term, text))
        for term in terms
    }
    idf = {term: math.log((total + 1) / (df[term] + 1)) + 1 for term in terms}
    by_page: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for index, text in enumerate(texts):
        coverage = sum(idf[term] for term in terms if _matches_coverage_term(term, text))
        if coverage:
            by_page[chunks[index][0]].append((coverage, index))
    scores = {}
    for page, items in by_page.items():
        items.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_index = items[0]
        scores[page] = (
            best_index,
            best_score + 0.15 * sum(score for score, _ in items[1:3]),
        )
    return scores


def dense_page_scores(
    chunks: list[tuple[int, str]], vectors: np.ndarray, query_vector: np.ndarray
) -> dict[int, tuple[int, float]]:
    denominator = np.linalg.norm(vectors, axis=1) * np.linalg.norm(query_vector)
    similarity = vectors @ query_vector / np.maximum(denominator, 1e-12)
    by_page: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for index, value in enumerate(similarity):
        by_page[chunks[index][0]].append((float(value), index))
    scores = {}
    for page, items in by_page.items():
        items.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_index = items[0]
        scores[page] = (
            best_index,
            best_score + 0.15 * sum(score for score, _ in items[1:3]),
        )
    return scores


def fuse_pages(
    lexical: dict[int, tuple[int, float]],
    dense: dict[int, tuple[int, float]],
    mode: str,
) -> dict[int, tuple[int, float]]:
    if mode == "lexical":
        return lexical
    if mode == "dense":
        return dense
    lex_order = {page: rank for rank, page in enumerate(sorted(lexical, key=lambda p: -lexical[p][1]), 1)}
    dense_order = {page: rank for rank, page in enumerate(sorted(dense, key=lambda p: -dense[p][1]), 1)}
    fused = {}
    for page in set(lexical) | set(dense):
        score = 1 / (60 + lex_order.get(page, 999)) + 1 / (60 + dense_order.get(page, 999))
        best_index = dense[page][0] if page in dense else lexical[page][0]
        fused[page] = (best_index, score)
    return fused


def rank_pages(
    page_scores: dict[int, tuple[int, float]], k: int = 5
) -> list[tuple[int, int, float]]:
    ranked = [
        (page, best_index, score)
        for page, (best_index, score) in page_scores.items()
    ]
    ranked.sort(key=lambda item: (-item[2], item[0]))
    return ranked[:k]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--docs", type=int, default=5)
    parser.add_argument("--questions", type=int, default=10)
    parser.add_argument("--strategy", choices=("section", "fixed"), default="section")
    parser.add_argument(
        "--extractor", choices=("pypdf", "pymupdf", "hybrid"), default="pypdf"
    )
    parser.add_argument("--chunk-size", type=int, default=2500)
    parser.add_argument("--parent-chars", type=int, default=2500)
    parser.add_argument(
        "--retrieval", choices=("lexical", "dense", "hybrid", "router"), default="lexical"
    )
    parser.add_argument(
        "--embedding-unit", choices=("chunk", "page"), default="chunk"
    )
    parser.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--embedding-path", type=Path)
    parser.add_argument(
        "--embedding-cache", type=Path, default=PDF_DIR.parent / "models"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "financebench-pdf.json")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_doc[row.get("doc_name", "")].append(row)
    selected_docs = [doc for doc in by_doc if by_doc[doc][0].get("doc_link")][: args.docs]

    chunk_page = _load_chunk_page()
    embedder = (
        TextEmbedding(
            model_name=args.embedding_model,
            cache_dir=str(args.embedding_cache),
            **(
                {"specific_model_path": str(args.embedding_path)}
                if args.embedding_path
                else {}
            ),
        )
        if args.retrieval in {"dense", "hybrid"}
        else None
    )
    results = []
    for doc in selected_docs:
        doc_rows = by_doc[doc]
        row0 = doc_rows[0]
        url = direct_pdf_url(row0.get("doc_link", ""))
        pdf_path = PDF_DIR / (re.sub(r"[^A-Za-z0-9_.-]+", "_", doc) + ".pdf")
        try:
            download_pdf(url, pdf_path)
            pages = page_texts(pdf_path, args.extractor)
        except Exception as exc:
            print(f"skip {doc}: {type(exc).__name__}: {exc}", flush=True)
            continue
        text_pages = [page for page in pages if len(page.strip()) > 100]
        if not text_pages:
            print(f"skip {doc}: no extractable text", flush=True)
            continue
        chunks: list[tuple[int, str]] = []
        for page_number, page in enumerate(pages):
            for chunk in chunk_page(
                page,
                args.strategy,
                args.chunk_size,
                120,
                company=row0.get("company", ""),
                form=row0.get("doc_type", ""),
                page=page_number,
            ):
                chunks.append((page_number, chunk))
        vectors = None
        if embedder is not None:
            if args.embedding_unit == "page":
                vectors = np.asarray(
                    list(embedder.embed([page[:4000] for page in pages], batch_size=16)),
                    dtype=np.float32,
                )
            else:
                vectors = np.asarray(
                    list(embedder.embed([text for _, text in chunks], batch_size=16)),
                    dtype=np.float32,
                )
        for row in doc_rows[: args.questions]:
            evidence = (row.get("evidence") or [{}])[0]
            fallback = int(evidence.get("evidence_page_num") or 0)
            gold_page = find_evidence_page(pages, evidence, fallback)
            if gold_page is None:
                continue
            terms = query_terms(row.get("Question") or row.get("question", ""))
            question = row.get("Question") or row.get("question", "")
            if args.retrieval == "router":
                page_scores = bm25_page_scores(pages, terms, question)
            else:
                lexical = (
                    lexical_page_scores(chunks, terms)
                    if args.retrieval in {"lexical", "hybrid"}
                    else {}
                )
                if args.retrieval == "lexical":
                    page_scores = lexical
                else:
                    expanded_query = " ".join(dict.fromkeys([question, *terms]))
                    query_vector = np.asarray(
                        list(embedder.query_embed(expanded_query))[0], dtype=np.float32
                    )
                    if args.embedding_unit == "page":
                        dense = page_dense_scores(pages, vectors, query_vector)
                    else:
                        dense = dense_page_scores(chunks, vectors, query_vector)
                    page_scores = fuse_pages(lexical, dense, args.retrieval)
            ranked = rank_pages(page_scores, 5)
            page_ranks = [position for position, (page, _, _) in enumerate(ranked, 1) if page == gold_page]
            snippet_evaluable = bool((evidence.get("evidence_text") or "").strip())
            snippet_ranks = []
            if snippet_evaluable:
                for position, (page, chunk_index, _) in enumerate(ranked, 1):
                    if args.embedding_unit == "page" or args.retrieval == "router":
                        parent = pages[page]
                    else:
                        chunk = chunks[chunk_index][1]
                        parent = expand_parent(pages[page], chunk, args.parent_chars)
                    if snippet_match(parent, evidence.get("evidence_text", "")):
                        snippet_ranks.append(position)
            results.append(
                {
                    "doc": doc,
                    "company": row.get("company"),
                    "question": row.get("question"),
                    "gold_page": gold_page,
                    "pages": len(pages),
                    "chunks": len(chunks),
                    "page_hit": bool(page_ranks),
                    "page_rank": page_ranks[0] if page_ranks else None,
                    "snippet_evaluable": snippet_evaluable,
                    "snippet_hit": bool(snippet_ranks),
                    "snippet_rank": snippet_ranks[0] if snippet_ranks else None,
                }
            )
            print(
                f"{doc} page={gold_page} page_rank={page_ranks[0] if page_ranks else '-'} "
                f"snippet_rank={snippet_ranks[0] if snippet_ranks else '-'}",
                flush=True,
            )

    n = len(results) or 1
    snippet_rows = [row for row in results if row.get("snippet_evaluable")]
    snippet_n = len(snippet_rows) or 1
    summary = {
        "questions": len(results),
        "docs": len({row['doc'] for row in results}),
        "page_hit_at_1": sum(1 for row in results if row["page_rank"] == 1) / n,
        "page_hit_at_3": sum(1 for row in results if row["page_rank"] and row["page_rank"] <= 3) / n,
        "page_hit_at_5": sum(row["page_hit"] for row in results) / n,
        "snippet_evaluable_questions": len(snippet_rows),
        "snippet_hit_at_1": sum(1 for row in results if row["snippet_rank"] == 1) / n,
        "snippet_hit_at_3": sum(1 for row in results if row["snippet_rank"] and row["snippet_rank"] <= 3) / n,
        "snippet_hit_at_5": sum(row["snippet_hit"] for row in results) / n,
        "snippet_hit_at_1_evaluable": sum(1 for row in snippet_rows if row["snippet_rank"] == 1) / snippet_n,
        "snippet_hit_at_3_evaluable": sum(1 for row in snippet_rows if row["snippet_rank"] and row["snippet_rank"] <= 3) / snippet_n,
        "snippet_hit_at_5_evaluable": sum(row["snippet_hit"] for row in snippet_rows) / snippet_n,
        "mrr_at_5": sum(1 / row["page_rank"] for row in results if row["page_rank"]) / n,
    }
    report = {
        "schema": "filingcopilot.financebench-pdf/v1",
        "dataset": "PatronusAI/financebench",
        "license": "CC-BY-NC-4.0",
        "scope": "full-filing PDF, evidence page/snippet",
        "strategy": args.strategy,
        "chunk_size": args.chunk_size,
        "parent_chars": args.parent_chars,
        "summary": summary,
        "rows": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
