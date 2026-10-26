"""Evaluate reference retrieval and answer support on FinDER.

FinDER rows are question -> reference excerpts + answer. The benchmark here is
deliberately reference-level, not full-filing page retrieval: we build a unique
reference corpus and check whether the retrieved reference contains the answer.

Data: Linq-AI-Research/FinDER, CC-BY-NC-4.0, 5,703 rows.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from filingcopilot.eval_support import classify_support

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evaluation" / "external" / "data" / "finder.parquet"
REPO = "Linq-AI-Research/FinDER"
REMOTE_FILE = "data/train-00000-of-00001.parquet"


def ensure_data(path: Path, force: bool = False) -> Path:
    if path.exists() and not force:
        return path
    from huggingface_hub import hf_hub_download

    path.parent.mkdir(parents=True, exist_ok=True)
    downloaded = hf_hub_download(
        repo_id=REPO,
        filename=REMOTE_FILE,
        repo_type="dataset",
    )
    shutil.copyfile(downloaded, path)
    return path


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def tokens(value: object) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(value))


def answer_support_report(texts: list[str], answer: object) -> dict:
    return classify_support(texts, answer).as_dict()


def answer_supported_text(text: object, answer: object) -> bool:
    """Compatibility helper: full support against one evidence text."""
    return answer_support_report([str(text)], answer)["status"] == "full"


def build_corpus(rows: list[dict]) -> tuple[list[str], dict[str, int]]:
    corpus: list[str] = []
    index: dict[str, int] = {}
    for row in rows:
        for reference in row["references"]:
            key = norm(reference)
            if key and key not in index:
                index[key] = len(corpus)
                corpus.append(str(reference))
    return corpus, index


COMPANY_GENERIC_PREFIXES = (
    "as of",
    "we ",
    "our ",
    "the company",
    "the most",
    "the board",
    "consolidated",
    "overview",
    "employees",
    "employee ",
    "table of contents",
    "one of",
    "at ",
    "income ",
    "net income",
    "workforce",
    "diversity",
    "talent",
    "board ",
    "corporate ",
    "sustainability",
    "future ",
    "basic ",
    "with ",
    "union ",
)
TICKER_STOP = {
    "FY",
    "GAAP",
    "EBIT",
    "EBITDA",
    "EPS",
    "AI",
    "US",
    "USA",
    "LLC",
    "INC",
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "SG&A",
    "CEO",
    "CFO",
    "ROI",
    "ROA",
    "YOY",
    "USD",
    "K",
    "M",
    "AR",
    "AP",
    "PPE",
    "R&D",
    "FTE",
    "HC",
    "DEI",
    "HCM",
    "NI",
    "SG",
    "OI",
    "GP",
    "GPM",
    "IBT",
    "CAGR",
    "OPEX",
    "NP",
    "COGS",
    "GM",
    "OM",
    "ROP",
    "CF",
    "NPM",
    "ESG",
    "TKR",
    "DBE",
    "HR",
    "TICKER",
}
ALIAS_STOP = {
    "the",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "company",
    "companies",
    "ltd",
    "limited",
    "plc",
    "holdings",
    "group",
    "and",
    "of",
    "a",
}
GENERIC_SINGLE_ALIASES = {
    "global",
    "capital",
    "delta",
    "american",
    "united",
    "general",
    "first",
    "international",
    "national",
    "public",
    "financial",
    "energy",
    "health",
    "systems",
    "solutions",
    "services",
    "industries",
    "resources",
    "community",
    "enterprise",
    "technology",
    "digital",
    "medical",
    "industrial",
    "consumer",
    "growth",
    "value",
    "income",
    "employee",
    "workforce",
    "diversity",
    "talent",
    "match",
    "pool",
    "meta",
}


def company_from_references(references: list[object]) -> str:
    """Return a company header from reference text, not from the answer."""
    for reference in references:
        lines = [line.strip() for line in str(reference).split("\n") if line.strip()]
        for line in lines[:4]:
            folded = line.casefold()
            if folded.startswith(COMPANY_GENERIC_PREFIXES):
                continue
            candidate = re.split(
                r"\b(?:and subsidiaries|and subsidiary companies|consolidated|overview)\b",
                line,
                flags=re.I,
            )[0].strip(' ,.|"')
            candidate = re.sub(r"\s+and\s*$", "", candidate, flags=re.I)
            if len(candidate) > 110 or len(candidate.split()) > 12:
                continue
            if ". " in candidate or ";" in candidate or "\n" in candidate:
                continue
            if not re.search(
                r"\b(inc|incorporated|corp|corporation|company|companies|ltd|limited|plc)\b",
                candidate.casefold(),
            ):
                continue
            return candidate
    return ""


def build_company_scope(
    rows: list[dict],
) -> tuple[
    dict[str, tuple[list[str], dict[str, int], list[list[str]], float]],
    dict[object, str],
    int,
]:
    """Build company groups from question tickers and reference headers.

    Company metadata is inferred from document headers and question text. The
    answer column is not used. Rows without a confident company fall back to
    their FinDER category.
    """
    ticker_companies: dict[str, Counter[str]] = defaultdict(Counter)
    parenthesized_tickers: set[str] = set()
    for row in rows:
        company = company_from_references(row["references"])
        if not company:
            continue
        text = str(row["text"])
        parenthesized_tickers.update(re.findall(r"\(([A-Z]{2,6})\)", text))
        for ticker in set(re.findall(r"\b[A-Z]{2,6}\b", text)):
            if ticker not in TICKER_STOP:
                ticker_companies[ticker][company] += 1

    ticker_map = {
        ticker: counter.most_common(1)[0][0]
        for ticker, counter in ticker_companies.items()
        if counter.most_common(1)[0][1] >= 2 or ticker in parenthesized_tickers
    }

    companies = Counter(
        company_from_references(row["references"]) for row in rows
    )
    alias_to_company: dict[str, str] = {}
    for company in companies:
        if not company:
            continue
        ascii_company = (
            unicodedata.normalize("NFKD", company)
            .encode("ascii", "ignore")
            .decode()
        )
        cleaned = re.sub(r"[^a-z0-9& ]+", " ", ascii_company.casefold())
        core = [
            word for word in cleaned.split() if word not in ALIAS_STOP
        ]
        for size in (1, 2, 3):
            if len(core) < size:
                continue
            alias = " ".join(core[:size])
            if len(alias) < 4:
                continue
            if size == 1 and alias in GENERIC_SINGLE_ALIASES:
                continue
            alias_to_company.setdefault(alias, company)
    aliases = sorted(alias_to_company, key=len, reverse=True)

    def company_for(row: dict) -> str:
        text = str(row["text"])
        for ticker in re.findall(r"\b[A-Z]{2,6}\b", text):
            if ticker in ticker_map:
                return ticker_map[ticker]
        folded = text.casefold()
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", folded):
                return alias_to_company[alias]
        return ""

    groups: dict[str, list[str]] = defaultdict(list)
    row_scope: dict[object, str] = {}
    for row in rows:
        company = company_for(row)
        if company:
            key = f"company:{company}"
        else:
            key = f"category:{row['category']}"
        groups[key].extend(str(reference) for reference in row["references"])
        row_scope[row["_id"]] = key

    corpora = {}
    for key, references in groups.items():
        corpus, index = build_corpus([{"references": references}])
        tokenized = [tokens(text) for text in corpus]
        average = sum(len(doc) for doc in tokenized) / max(1, len(tokenized))
        corpora[key] = (corpus, index, tokenized, average)
    company_scopes = sum(1 for key in groups if key.startswith("company:"))
    return corpora, row_scope, company_scopes


def build_scoped_corpora(
    rows: list[dict], scope: str
) -> dict[str, tuple[list[str], dict[str, int], list[list[str]], float]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        key = "global" if scope == "global" else str(row["category"])
        groups[key].extend(list(row["references"]))
    corpora = {}
    for key, references in groups.items():
        corpus, index = build_corpus([{"references": references}])
        tokenized = [tokens(text) for text in corpus]
        average = sum(len(doc) for doc in tokenized) / max(1, len(tokenized))
        corpora[key] = (corpus, index, tokenized, average)
    return corpora


def bm25_top(
    tokenized: list[list[str]], avg_length: float, terms: list[str], k: int = 5
) -> list[tuple[int, float]]:
    total = len(tokenized)
    df = {term: sum(1 for doc in tokenized if term in doc) for term in terms}
    idf = {
        term: math.log((total - df[term] + 0.5) / (df[term] + 0.5) + 1)
        for term in terms
    }
    scored = []
    for index, doc in enumerate(tokenized):
        if not doc:
            continue
        length = len(doc)
        tf = {term: doc.count(term) for term in terms}
        score = sum(
            idf[term]
            * tf[term]
            * 2.2
            / (tf[term] + 1.2 * (1 - 0.75 + 0.75 * length / avg_length))
            for term in terms
            if tf[term]
        )
        if score:
            scored.append((index, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:k]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "finder-eval.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-reasoning", action="store_true")
    parser.add_argument(
        "--scope", choices=("global", "category", "company"), default="company"
    )
    parser.add_argument("--rerank", choices=("off", "flashrank"), default="flashrank")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    data_path = ensure_data(args.data)
    frame = pd.read_parquet(data_path)
    all_rows = frame.to_dict("records")
    row_scope: dict[object, str] | None = None
    company_scopes = 0
    if args.scope == "company":
        corpora, row_scope, company_scopes = build_company_scope(all_rows)
    else:
        corpora = build_scoped_corpora(all_rows, args.scope)
    rows = (
        all_rows
        if args.include_reasoning
        else [row for row in all_rows if row["reasoning"]]
    )
    if args.limit:
        rows = rows[: args.limit]

    ranker = None
    if args.rerank == "flashrank":
        from flashrank import Ranker

        ranker = Ranker(
            model_name="ms-marco-MiniLM-L-12-v2",
            cache_dir=str(ROOT / "models" / "flashrank"),
        )

    results = []
    latencies = []
    company_scoped_questions = 0
    for row in rows:
        started = time.perf_counter()
        if row_scope is not None:
            scope_key = row_scope[row["_id"]]
            if scope_key.startswith("company:"):
                company_scoped_questions += 1
        else:
            scope_key = "global" if args.scope == "global" else str(row["category"])
        corpus, corpus_index, tokenized_corpus, avg_length = corpora[scope_key]
        top = bm25_top(tokenized_corpus, avg_length, tokens(row["text"]), args.k)
        if ranker is not None and top:
            from flashrank import RerankRequest

            passages = [
                {"id": index, "text": corpus[reference][:2000]}
                for index, (reference, _) in enumerate(top)
            ]
            reranked = ranker.rerank(
                RerankRequest(query=str(row["text"]), passages=passages)
            )
            order = [item["id"] for item in reranked]
            top = [top[index] for index in order if index < len(top)]
        gold = {
            corpus_index[norm(reference)]
            for reference in row["references"]
            if norm(reference) in corpus_index
        }
        ranks = [rank for rank, (index, _) in enumerate(top, 1) if index in gold]
        top1_report = answer_support_report(
            [corpus[top[0][0]]] if top else [], row["answer"]
        )
        at5_report = answer_support_report(
            [corpus[index] for index, _ in top], row["answer"]
        )
        evaluable_report = answer_support_report(row["references"], row["answer"])
        results.append(
            {
                "id": row["_id"],
                "question": row["text"],
                "category": row["category"],
                "answer": row["answer"],
                "answer_evaluable": evaluable_report["status"] == "full",
                "answer_evaluable_status": evaluable_report["status"],
                "reference_hit": bool(ranks),
                "reference_rank": ranks[0] if ranks else None,
                "answer_supported_top1": top1_report["status"] == "full",
                "answer_partial_top1": top1_report["status"] == "partial",
                "answer_unsupported_top1": top1_report["status"] == "unsupported",
                "answer_support_top1": top1_report,
                "answer_supported_at_5": at5_report["status"] == "full",
                "answer_partial_at_5": at5_report["status"] == "partial",
                "answer_unsupported_at_5": at5_report["status"] == "unsupported",
                "answer_support_at_5": at5_report,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
        )
        latencies.append(results[-1]["elapsed_ms"])
        if len(results) % 200 == 0:
            print(f"done {len(results)}", flush=True)

    n = len(results) or 1
    evaluable = [row for row in results if row["answer_evaluable"]]
    partial_evaluable = [
        row for row in results if row["answer_evaluable_status"] == "partial"
    ]
    unsupported_evaluable = [
        row for row in results if row["answer_evaluable_status"] == "unsupported"
    ]
    ne = len(evaluable) or 1
    durations = sorted(latencies)
    summary = {
        "questions": len(results),
        "scope": args.scope,
        "company_scopes": company_scopes,
        "company_scoped_questions": company_scoped_questions,
        "corpus_references": sum(len(item[0]) for item in corpora.values()),
        "reference_hit_at_1": sum(
            1 for row in results if row["reference_rank"] == 1
        )
        / n,
        "reference_hit_at_5": sum(row["reference_hit"] for row in results) / n,
        "answer_supported_top1": sum(row["answer_supported_top1"] for row in results)
        / n,
        "answer_partial_top1": sum(row["answer_partial_top1"] for row in results) / n,
        "answer_unsupported_top1": sum(
            row["answer_unsupported_top1"] for row in results
        )
        / n,
        "answer_supported_at_5": sum(row["answer_supported_at_5"] for row in results)
        / n,
        "answer_partial_at_5": sum(row["answer_partial_at_5"] for row in results) / n,
        "answer_unsupported_at_5": sum(
            row["answer_unsupported_at_5"] for row in results
        )
        / n,
        "answer_evaluable_questions": len(evaluable),
        "answer_partial_evaluable_questions": len(partial_evaluable),
        "answer_unsupported_evaluable_questions": len(unsupported_evaluable),
        "answer_supported_top1_evaluable": sum(
            row["answer_supported_top1"] for row in evaluable
        )
        / ne,
        "answer_supported_at_5_evaluable": sum(
            row["answer_supported_at_5"] for row in evaluable
        )
        / ne,
        "p50_ms": durations[len(durations) // 2] if durations else 0,
        "p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        if durations
        else 0,
    }
    report = {
        "schema": "filingcopilot.finder-eval/v1",
        "dataset": REPO,
        "license": "CC-BY-NC-4.0",
        "scope": (
            "reference retrieval "
            f"({args.scope} scope, category fallback for unscoped rows); "
            "reasoning rows excluded unless --include-reasoning"
        ),
        "rerank": args.rerank,
        "support_definition": (
            "full requires every key number, unit and period in one evidence set; "
            "verified calculation inputs may support a result that is not copied "
            "verbatim; partial means some required values are present; unsupported "
            "means no required number is covered"
        ),
        "summary": summary,
        "rows": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
