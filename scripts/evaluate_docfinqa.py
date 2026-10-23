"""Compare chunking strategies on the DocFinQA long-context test subset.

DocFinQA has no evidence span, so this measures a weaker proxy: whether the gold
answer string appears in the top-k chunk (or its 2,500-character parent window).
It is not exact-evidence retrieval and not answer accuracy. Its purpose is to test
whether the chunking ranking transfers from FinanceBench to long 10-K/10-Q text.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import time
from pathlib import Path

from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.retrieval import _coverage_text, _matches_coverage_term, rewrite_query
from filingcopilot.retrieval_query import lexical_terms, retrieval_text

ROOT = Path(__file__).resolve().parents[1]
FB_PATH = ROOT / "scripts" / "evaluate_financebench.py"
DEFAULT_DATA = ROOT / "evaluation" / "external" / "data" / "docfinqa.jsonl"


def _load_chunk_page():
    spec = importlib.util.spec_from_file_location("financebench_eval", FB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.chunk_page


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def answer_in_text(text: str, answer: str) -> bool:
    text = norm(text)
    answer = norm(answer)
    if not answer:
        return False
    if re.fullmatch(r"[-+]?\$?\d[\d,.]*%?", answer):
        return re.search(r"(?<![\d.])" + re.escape(answer) + r"(?![\d.])", text) is not None
    return answer in text


def expand_parent(context: str, chunk: str, parent_chars: int) -> str:
    index = context.find(chunk)
    if index < 0:
        index = context.find(chunk[:200])
    if index < 0:
        return chunk
    start = max(0, index - parent_chars // 4)
    end = min(len(context), start + parent_chars)
    return context[start:end]


def query_terms(question: str) -> list[str]:
    rewrite = rewrite_query(question)
    focused = retrieval_text(rewrite["expanded"], DEFAULT_DOMAIN, None, None)
    return lexical_terms(focused)


def rank_chunks(chunks: list[str], terms: list[str], k: int = 5) -> list[tuple[int, float]]:
    texts = [_coverage_text(chunk) for chunk in chunks]
    total = len(texts)
    df = {
        term: sum(1 for text in texts if _matches_coverage_term(term, text))
        for term in terms
    }
    idf = {term: math.log((total + 1) / (df[term] + 1)) + 1 for term in terms}
    scored = []
    for index, text in enumerate(texts):
        coverage = sum(idf[term] for term in terms if _matches_coverage_term(term, text))
        if coverage:
            scored.append((index, coverage))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:k]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--parent-chars", type=int, default=2500)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "docfinqa-chunking.json")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = rows[: args.limit]
    chunk_page = _load_chunk_page()
    strategies = [
        ("fixed", 1200),
        ("fixed", 2500),
        ("recursive", 2500),
        ("section", 2500),
        ("section_table", 2500),
        ("table", 2500),
        ("sentence", 2500),
        ("child", 600),
    ]
    results = {}
    for strategy, size in strategies:
        label = f"{strategy}-{size}"
        rows_out = []
        started = time.perf_counter()
        for row in rows:
            context = row.get("Context", "")
            chunks = chunk_page(
                context,
                strategy,
                size,
                120,
                company="DocFinQA",
                form="10-K",
                page=1,
            )
            terms = query_terms(row.get("Question", ""))
            ranked = rank_chunks(chunks, terms, args.k)
            answer = row.get("Answer", "")
            chunk_ranks = [
                rank
                for rank, (index, _) in enumerate(ranked, 1)
                if answer_in_text(chunks[index], answer)
            ]
            parent_ranks = [
                rank
                for rank, (index, _) in enumerate(ranked, 1)
                if answer_in_text(expand_parent(context, chunks[index], args.parent_chars), answer)
            ]
            rows_out.append(
                {
                    "question": row.get("Question"),
                    "answer": answer,
                    "context_chars": len(context),
                    "chunks": len(chunks),
                    "avg_chunk_chars": round(sum(len(c) for c in chunks) / len(chunks), 1) if chunks else 0,
                    "chunk_hit": bool(chunk_ranks),
                    "chunk_rank": chunk_ranks[0] if chunk_ranks else None,
                    "parent_hit": bool(parent_ranks),
                    "parent_rank": parent_ranks[0] if parent_ranks else None,
                }
            )
        n = len(rows_out) or 1
        durations = (time.perf_counter() - started) * 1000
        results[label] = {
            "strategy": strategy,
            "chunk_size": size,
            "questions": len(rows_out),
            "chunk_hit_at_1": sum(1 for r in rows_out if r["chunk_rank"] == 1) / n,
            "chunk_hit_at_3": sum(1 for r in rows_out if r["chunk_rank"] and r["chunk_rank"] <= 3) / n,
            "chunk_hit_at_5": sum(r["chunk_hit"] for r in rows_out) / n,
            "parent_hit_at_1": sum(1 for r in rows_out if r["parent_rank"] == 1) / n,
            "parent_hit_at_3": sum(1 for r in rows_out if r["parent_rank"] and r["parent_rank"] <= 3) / n,
            "parent_hit_at_5": sum(r["parent_hit"] for r in rows_out) / n,
            "mrr_at_5": sum(1 / r["chunk_rank"] for r in rows_out if r["chunk_rank"]) / n,
            "avg_chunks": round(sum(r["chunks"] for r in rows_out) / n, 1),
            "avg_chunk_chars": round(sum(r["avg_chunk_chars"] for r in rows_out) / n, 1),
            "avg_context_chars": round(sum(r["context_chars"] for r in rows_out) / n),
            "elapsed_ms": round(durations, 1),
            "rows": rows_out,
        }
        print(
            f"{label:18} chunk@5={results[label]['chunk_hit_at_5']:.2f} "
            f"parent@5={results[label]['parent_hit_at_5']:.2f} "
            f"mrr={results[label]['mrr_at_5']:.3f} "
            f"avg_chunks={results[label]['avg_chunks']:.0f} "
            f"avg_chars={results[label]['avg_chunk_chars']:.0f}",
            flush=True,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
