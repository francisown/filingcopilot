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
from filingcopilot.models import Filing, Passage
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings

TICKER = "FB"
AS_OF = date(2024, 12, 31)
FILED = date(2024, 1, 1)
PERIOD_END = date(2023, 12, 31)
DEFAULT_DATA = Path("evaluation/external/data/financebench.jsonl")


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or len(text) <= size:
        return [text]
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


def build_corpus(rows: list[dict], home: Path, *, chunk_size: int = 0, chunk_overlap: int = 120) -> dict:
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)
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
    for doc, page_map in pages.items():
        row = next(iter(page_map.values()))["row"]
        filing = Filing(
            id=doc,
            ticker=TICKER,
            company=row.get("company") or doc,
            cik="0000000000",
            accession=doc,
            form=row.get("doc_type") or "filing",
            filed=FILED,
            period_end=PERIOD_END,
            source_url=row.get("doc_link") or "",
            content_sha256=hashlib.sha256(doc.encode()).hexdigest(),
        )
        passages = []
        for page, payload in sorted(page_map.items()):
            chunks = chunk_text(payload["text"], chunk_size, chunk_overlap)
            for index, chunk in enumerate(chunks):
                passages.append(
                    Passage(
                        id=stable_id("passage", doc, page, index, chunk[:80]),
                        filing_id=doc,
                        ticker=TICKER,
                        section=f"{filing.company} | {filing.form} | page {page}",
                        text=chunk,
                        source_url=filing.source_url,
                        locator=f"page {page} chunk {index}",
                    )
                )
        corpus.ingest(filing, [], passages)
        passages_total += len(passages)
    return {"pages": sum(len(page_map) for page_map in pages.values()), "passages": passages_total}


def page_hit(hit: dict, gold_pages: set[tuple[str, int]]) -> bool:
    passage = hit["passage"]
    page_match = re.search(r"\d+", passage.get("locator", ""))
    page = int(page_match.group()) if page_match else None
    return (passage.get("filing_id"), page) in gold_pages


def snippet_hit(hit: dict, snippets: list[str]) -> bool:
    text = norm(hit["passage"]["text"])
    for snippet in snippets:
        if not snippet:
            continue
        if norm(snippet) in text:
            return True
        wanted = tokens(snippet)
        if wanted and len(wanted & tokens(text)) / len(wanted) >= 0.90:
            return True
    return False


def answer_support(hit: dict, answer: str) -> bool:
    text = norm(hit["passage"]["text"])
    if not answer:
        return False
    numbers = re.findall(r"[-+]?\d[\d,.]*", answer)
    if numbers:
        for number in numbers:
            cleaned = number.replace(",", "").rstrip(".")
            if cleaned and cleaned in text.replace(",", ""):
                return True
    return norm(answer)[:120] in text


def evaluate(rows: list[dict], retriever: Retriever, k: int = 5) -> dict:
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
        started = time.perf_counter()
        result = retriever.search(row["question"], AS_OF, ticker=TICKER, k=k)
        elapsed = (time.perf_counter() - started) * 1000
        hits = result["hits"][:k]
        page_ranks = [rank for rank, hit in enumerate(hits, 1) if page_hit(hit, gold_pages)]
        snippet_ranks = [rank for rank, hit in enumerate(hits, 1) if snippet_hit(hit, snippets)]
        results.append(
            {
                "id": row["financebench_id"],
                "company": row.get("company"),
                "doc_name": row.get("doc_name"),
                "doc_type": row.get("doc_type"),
                "question_type": row.get("question_type"),
                "question": row["question"],
                "answer": row.get("answer"),
                "page_hit": bool(page_ranks),
                "snippet_hit": bool(snippet_ranks),
                "page_rank": page_ranks[0] if page_ranks else None,
                "snippet_rank": snippet_ranks[0] if snippet_ranks else None,
                "answer_supported_top1": bool(hits) and answer_support(hits[0], row.get("answer", "")),
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
    parser.add_argument("--limit", type=int)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--mode", choices=("coverage", "anchor", "rrf"), default="coverage")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument("--output", type=Path, default=Path("artifacts/financebench-eval.json"))
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = rows[: args.limit] if args.limit else rows
    corpus_info = build_corpus(
        rows,
        args.home,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    settings = Settings(home=args.home, embedding_mode="off", rerank_mode=args.mode)
    retriever = Retriever(Corpus(args.home), settings)
    report = evaluate(rows, retriever, args.k)
    payload = {
        "schema": "filingcopilot.financebench-eval/v1",
        "dataset": "PatronusAI/financebench",
        "license": "CC-BY-NC-4.0",
        "scope": "evidence-page retrieval, not full-filing retrieval",
        "mode": args.mode,
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
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
