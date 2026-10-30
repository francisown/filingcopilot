"""Run a reproducible retrieval ablation against a frozen gold JSON file."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings

_EQUIVALENCE_CACHE: dict[str, set[str]] = {}
_SPAN_CACHE: dict[str, set[str]] = {}
_PAGE_CACHE: dict[str, int | None] = {}
_NUGGET_CACHE: dict[str, list[str]] = {}

# An eight-word span survives re-chunking, quote-style changes and whitespace
# repair. Industry guidance is explicit that gold evidence must be recorded as
# spans or quotes rather than chunk ids, because a chunking change otherwise
# invalidates the dataset silently.
SPAN_WORDS = 8


def _spans(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9%$]+", str(text).lower())
    if len(words) < SPAN_WORDS:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + SPAN_WORDS]) for i in range(len(words) - SPAN_WORDS + 1)}


_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*%?")
_ENTITY = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


def gold_nuggets(corpus, item: dict) -> list[str]:
    """Atomic evidence points a complete answer must contain.

    A single pinned chunk cannot express "this answer needs the 2.02% growth and
    the Greater China line". Nuggets are the distinctive numbers and multi-word
    entities of the gold quote, kept only when they are rare inside the filing so
    that generic words do not count as coverage.
    """
    cached = _NUGGET_CACHE.get(item.get("id", ""))
    if cached is not None:
        return cached
    quotes = item.get("expected_quotes", [])
    scope = corpus.passages(date(2026, 10, 10), item["ticker"], item["fiscal_year"])
    folded = [passage.text for passage in scope]
    candidates: list[str] = []
    for quote in quotes:
        for match in _NUMBER.finditer(quote):
            token = match.group(0).replace(" ", "").replace("$", "")
            if len(token) >= 4 or token.endswith("%"):
                candidates.append(token)
        for match in _ENTITY.finditer(quote):
            candidates.append(match.group(0))
    kept: list[str] = []
    for token in dict.fromkeys(candidates):
        probe = token.rstrip("%").strip()
        if len(probe) < 3:
            continue
        support = sum(1 for text in folded if probe in text)
        if 0 < support <= 40:
            kept.append(token)
    _NUGGET_CACHE[item.get("id", "")] = kept[:8]
    return _NUGGET_CACHE[item.get("id", "")]


def gold_page(corpus, item: dict) -> int | None:
    """Page of the passage that carries the gold quote, in the current corpus.

    Page-level recall is the metric the FinanceBench literature reports, and it
    tolerates the paragraph-level ambiguity that a pinned chunk id cannot.
    """
    cached = _PAGE_CACHE.get(item.get("id", ""), "unset")
    if cached != "unset":
        return cached
    page = None
    quotes = item.get("expected_quotes", [])
    if quotes:
        probe = set()
        for quote in quotes:
            probe |= _spans(quote)
        scope = corpus.passages(date(2026, 10, 10), item["ticker"], item["fiscal_year"])
        best_overlap, best_page = 0, None
        for passage in scope:
            if not getattr(passage, "page", None):
                continue
            overlap = len(probe & _spans(passage.text))
            # The best-contained passage, not the first mention: filings repeat
            # cross-references and table-of-contents lines on many pages.
            if overlap > best_overlap:
                best_overlap, best_page = overlap, passage.page
        page = best_page
    _PAGE_CACHE[item.get("id", "")] = page
    return page


def gold_spans(corpus, item: dict) -> set[str]:
    cached = _SPAN_CACHE.get(item.get("id", ""))
    if cached is not None:
        return cached
    spans: set[str] = set()
    # Quotes are the durable label: they survive a parser or chunker change that
    # invalidates every stored chunk id. Fall back to the passage text only for
    # older records that predate the quote annotation.
    for quote in item.get("expected_quotes", []):
        spans |= _spans(quote)
    if not spans:
        for passage_id in item.get("expected_passage_ids", []):
            try:
                evidence = corpus.evidence(passage_id, date(2026, 10, 10))
            except Exception:
                continue
            if evidence:
                spans |= _spans(evidence.get("text", ""))
    _SPAN_CACHE[item.get("id", "")] = spans
    return spans


def score(item: dict, result: dict, k: int) -> tuple[float, float]:
    hits = result.get("hits", [])[:k]
    expected = set(item.get("expected_passage_ids", []))
    if expected:
        ranks = [rank for rank, hit in enumerate(hits, 1) if hit["passage"]["id"] in expected]
    else:
        section = item.get("gold_context", {}).get("section", "")
        terms = [str(term).casefold() for term in item.get("gold_context", {}).get("anchor_terms", [])]
        ranks = [
            rank
            for rank, hit in enumerate(hits, 1)
            if section.casefold() in hit["passage"].get("section", "").casefold()
            or sum(term in hit["passage"].get("text", "").casefold() for term in terms) >= 2
        ]
    return (1.0 if ranks else 0.0, 1.0 / ranks[0] if ranks else 0.0)


def equivalence_ids(corpus, item: dict) -> set[str]:
    """Passages that satisfy the gold's own anchor_terms inside its own scope.

    A single pinned passage id makes the metric depend on which of several
    equally-qualifying paragraphs the editor happened to pick. The size of this
    set is the ceiling of that metric: with ``n`` equivalents and ``k`` slots, a
    system that ranks randomly among equivalents scores about ``k / n``.
    """
    cached = _EQUIVALENCE_CACHE.get(item.get("id", ""))
    if cached is not None:
        return cached
    terms = [
        str(term).casefold()
        for term in item.get("gold_context", {}).get("anchor_terms", [])
    ]
    if not terms:
        return set()
    scope = corpus.passages(date(2026, 10, 10), item["ticker"], item["fiscal_year"])
    found = {
        passage.id
        for passage in scope
        if sum(1 for term in terms if term in passage.text.casefold()) >= 2
    }
    _EQUIVALENCE_CACHE[item.get("id", "")] = found
    return found


def run_variant(
    retriever: Retriever,
    items: list[dict],
    *,
    lexical_only: bool,
    rerank_mode: str = "anchor",
    anchor_weight: float = 0.01,
) -> dict:
    recalls, mrrs, durations = [], [], []
    equivalence_recalls, equivalence_sizes = [], []
    span_ranks: list[int | None] = []
    page_ranks: list[int | None] = []
    page_labels = 0
    nugget_scores: list[float] = []
    backends = Counter()
    dense_hit_rows = 0
    embedding_errors = Counter()
    for item in items:
        started = time.perf_counter()
        result = retriever.search(
            item["query"],
            date(2026, 10, 10),
            ticker=item["ticker"],
            k=5,
            fiscal_year=item["fiscal_year"],
            lexical_only=lexical_only,
            rerank_mode=rerank_mode,
            anchor_weight=anchor_weight,
        )
        # Time the retrieval call only; label-equivalence work is analysis cost.
        durations.append((time.perf_counter() - started) * 1000)
        backends[result["backend"]] += 1
        dense_hit_rows += any("dense" in hit.get("legs", {}) for hit in result.get("hits", []))
        if result.get("embedding_unavailable"):
            embedding_errors[result["embedding_unavailable"]] += 1
        recalls.append(score(item, result, 5)[0])
        spans = gold_spans(retriever.corpus, item)
        span_rank = None
        if spans:
            for rank, hit in enumerate(result.get("hits", [])[:5], 1):
                if spans & _spans(hit["passage"].get("text", "")):
                    span_rank = rank
                    break
        span_ranks.append(span_rank)
        target_page = gold_page(retriever.corpus, item)
        found_page = None
        if target_page:
            page_labels += 1
            for rank, hit in enumerate(result.get("hits", [])[:5], 1):
                if hit["passage"].get("page") == target_page:
                    found_page = rank
                    break
        page_ranks.append(found_page)
        nuggets = gold_nuggets(retriever.corpus, item)
        if nuggets:
            joined = " ".join(hit["passage"].get("text", "") for hit in result.get("hits", [])[:5])
            found = sum(1 for token in nuggets if token.rstrip("%") in joined)
            nugget_scores.append(found / len(nuggets))
        mrrs.append(score(item, result, 5)[1])
        equivalents = equivalence_ids(retriever.corpus, item)
        if equivalents:
            ids = {hit["passage"]["id"] for hit in result.get("hits", [])[:5]}
            equivalence_sizes.append(len(equivalents))
            equivalence_recalls.append(1.0 if equivalents & ids else 0.0)
    n = len(items) or 1
    durations.sort()
    equivalence_sizes.sort()
    median_equivalents = (
        equivalence_sizes[len(equivalence_sizes) // 2] if equivalence_sizes else 0
    )
    return {
        "questions": len(items),
        "recall_at_5": round(sum(recalls) / n, 6),
        "mrr_at_5": round(sum(mrrs) / n, 6),
        "span_recall_at_5": round(
            sum(1 for rank in span_ranks if rank and rank <= 5) / (len(span_ranks) or 1), 6
        ),
        "span_mrr_at_5": round(
            sum(1.0 / rank for rank in span_ranks if rank and rank <= 5)
            / (len(span_ranks) or 1),
            6,
        ),
        "page_recall_at_5": round(
            sum(1 for rank in page_ranks if rank) / (page_labels or 1), 6
        ),
        "page_questions_with_label": page_labels,
        "nugget_recall_at_5": round(sum(nugget_scores) / (len(nugget_scores) or 1), 6),
        "nugget_questions": len(nugget_scores),
        "span_scope": (
            f"gold evidence matched by any {SPAN_WORDS}-word span in a delivered top-5 "
            "passage; survives re-chunking, unlike a chunk id"
        ),
        "equivalence_recall_at_5": (
            round(sum(equivalence_recalls) / len(equivalence_recalls), 6)
            if equivalence_recalls
            else None
        ),
        "label_ceiling": {
            "median_equivalent_passages": median_equivalents,
            "single_id_upper_bound_at_5": (
                round(5 / median_equivalents, 6) if median_equivalents else None
            ),
            "scope": "a random ranking among equivalents scores about k / equivalents",
        },
        "backend_counts": dict(backends),
        "rows_with_dense_hit": dense_hit_rows,
        "embedding_errors": dict(embedding_errors),
        "p50_ms": round(durations[len(durations) // 2], 3) if durations else 0,
        "p95_ms": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 3)
        if durations
        else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(".filingcopilot"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/retrieval_gold_v2.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/retrieval-ablation.json"))
    parser.add_argument("--limit", type=int, help="Run a deterministic prefix for a quick pilot")
    parser.add_argument("--embedding-mode", choices=("local", "off"), default="local")
    parser.add_argument(
        "--rerank-mode",
        choices=("rrf", "anchor", "coverage", "diversity"),
        default="anchor",
    )
    parser.add_argument(
        "--multi-query",
        choices=("on", "off"),
        default="on",
        help="Thematic sub-query leg; keep on to describe the shipped system.",
    )
    parser.add_argument(
        "--anchor-weight",
        type=float,
        default=0.05,
        help="Must match the product default in Retriever.search, or the report describes a different system.",
    )
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    items = gold["items"][: args.limit] if args.limit else gold["items"]
    retriever = Retriever(
        Corpus(args.home),
        Settings(
            home=args.home,
            embedding_mode=args.embedding_mode,
            retrieval_multi_query=args.multi_query == "on",
        ),
    )
    fts = run_variant(
        retriever,
        items,
        lexical_only=True,
        rerank_mode=args.rerank_mode,
        anchor_weight=args.anchor_weight,
    )
    hybrid = run_variant(
        retriever,
        items,
        lexical_only=False,
        rerank_mode=args.rerank_mode,
        anchor_weight=args.anchor_weight,
    )
    dense_available = hybrid["backend_counts"].get("fts5+bge+rrf", 0) > 0
    recall_delta = round(hybrid["recall_at_5"] - fts["recall_at_5"], 6)
    mrr_delta = round(hybrid["mrr_at_5"] - fts["mrr_at_5"], 6)
    improved = dense_available and recall_delta > 0 and mrr_delta >= 0
    tradeoff = dense_available and recall_delta > 0 and mrr_delta < 0
    baseline = None
    baseline_delta = None
    baseline_improved = False
    baseline_tradeoff = False
    if args.rerank_mode in {"anchor", "diversity"}:
        baseline_mode = "rrf" if args.rerank_mode == "anchor" else "anchor"
        baseline_fts = run_variant(
            retriever,
            items,
            lexical_only=True,
            rerank_mode=baseline_mode,
            anchor_weight=0.0 if baseline_mode == "rrf" else args.anchor_weight,
        )
        baseline_hybrid = run_variant(
            retriever,
            items,
            lexical_only=False,
            rerank_mode=baseline_mode,
            anchor_weight=0.0 if baseline_mode == "rrf" else args.anchor_weight,
        )
        baseline = {
            "rerank_mode": baseline_mode,
            "fts5": baseline_fts,
            "hybrid_rrf": baseline_hybrid,
        }
        baseline_recall_delta = round(
            hybrid["recall_at_5"] - baseline_hybrid["recall_at_5"], 6
        )
        baseline_mrr_delta = round(hybrid["mrr_at_5"] - baseline_hybrid["mrr_at_5"], 6)
        baseline_delta = {
            "recall_at_5": baseline_recall_delta,
            "mrr_at_5": baseline_mrr_delta,
            "span_recall_at_5": round(
                hybrid["span_recall_at_5"] - baseline_hybrid["span_recall_at_5"], 6
            ),
            "page_recall_at_5": round(
                hybrid["page_recall_at_5"] - baseline_hybrid["page_recall_at_5"], 6
            ),
            "nugget_recall_at_5": round(
                hybrid["nugget_recall_at_5"] - baseline_hybrid["nugget_recall_at_5"], 6
            ),
        }
        baseline_improved = (
            baseline_delta["span_recall_at_5"] >= 0
            and baseline_delta["page_recall_at_5"] >= 0
            and baseline_delta["nugget_recall_at_5"] >= 0
            and baseline_mrr_delta >= -0.001
            and (
                baseline_delta["span_recall_at_5"] > 0
                or baseline_delta["page_recall_at_5"] > 0
                or baseline_delta["nugget_recall_at_5"] > 0
            )
        )
        baseline_tradeoff = not baseline_improved and (
            baseline_delta["span_recall_at_5"] < 0
            or baseline_delta["page_recall_at_5"] < 0
            or baseline_delta["nugget_recall_at_5"] < 0
        )
    result = {
        "schema": "filingcopilot.retrieval-ablation/v1",
        "gold": {"path": str(args.gold), "version": gold.get("version"), "questions": len(items)},
        "retriever": {
            "embedding_mode": args.embedding_mode,
            "rerank_mode": args.rerank_mode,
            "anchor_weight": args.anchor_weight,
            "k": 5,
        },
        "variants": {
            "fts5": fts,
            "hybrid_rrf": hybrid,
        },
        "hybrid_delta": {
            "recall_at_5": recall_delta,
            "mrr_at_5": mrr_delta,
        },
        **({"baseline": baseline} if baseline else {}),
        **({"baseline_delta": baseline_delta} if baseline_delta else {}),
        "not_integrated": [
            "neural_query_rewrite",
            "multilingual_embedding",
            "cross_encoder_rerank",
        ],
        "decision": (
            "enable_diversity_rerank"
            if args.rerank_mode == "diversity" and baseline_improved
            else "diversity_rerank_tradeoff"
            if args.rerank_mode == "diversity" and baseline_tradeoff
            else "diversity_rerank_no_reproducible_gain"
            if args.rerank_mode == "diversity" and baseline is not None
            else "lexical_only_baseline_not_a_hybrid_comparison"
            if args.embedding_mode == "off"
            else "enable_anchor_rerank"
            if baseline_improved
            else "anchor_rerank_recall_gain_mrr_tradeoff"
            if baseline_tradeoff
            else "anchor_rerank_no_reproducible_gain"
            if baseline is not None
            else "retain_hybrid_rrf_with_reproducible_gain"
            if improved
            else "hybrid_recall_gain_mrr_tradeoff"
            if tradeoff
            else "retain_hybrid_rrf_no_reproducible_gain"
            if dense_available
            else "hybrid_unavailable"
        ),
        "notes": [
            "The hybrid variant is only comparable when its backend reports fts5+bge+rrf.",
            "Use --limit for a bounded pilot; local embedding generation is intentionally measured in the report.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
