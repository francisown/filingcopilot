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
from collections import defaultdict
from pathlib import Path

import pandas as pd

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


def answer_supported_text(text: object, answer: object) -> bool:
    text_value = norm(text)
    answer_value = norm(answer)
    if not text_value or not answer_value:
        return False
    numbers = re.findall(r"[-+]?\d[\d,.]*", answer_value)
    if numbers:
        for number in numbers:
            cleaned = number.replace(",", "").rstrip(".")
            if cleaned and cleaned in text_value.replace(",", ""):
                return True
    return answer_value[:120] in text_value


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
    parser.add_argument("--scope", choices=("global", "category"), default="category")
    parser.add_argument("--rerank", choices=("off", "flashrank"), default="flashrank")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    data_path = ensure_data(args.data)
    frame = pd.read_parquet(data_path)
    all_rows = frame.to_dict("records")
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
    for row in rows:
        started = time.perf_counter()
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
        answer_top1 = bool(top) and answer_supported_text(
            corpus[top[0][0]], row["answer"]
        )
        answer_at5 = any(
            answer_supported_text(corpus[index], row["answer"]) for index, _ in top
        )
        answer_evaluable = any(
            answer_supported_text(reference, row["answer"])
            for reference in row["references"]
        )
        results.append(
            {
                "id": row["_id"],
                "question": row["text"],
                "category": row["category"],
                "answer": row["answer"],
                "answer_evaluable": answer_evaluable,
                "reference_hit": bool(ranks),
                "reference_rank": ranks[0] if ranks else None,
                "answer_supported_top1": answer_top1,
                "answer_supported_at_5": answer_at5,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
        )
        latencies.append(results[-1]["elapsed_ms"])
        if len(results) % 200 == 0:
            print(f"done {len(results)}", flush=True)

    n = len(results) or 1
    evaluable = [row for row in results if row["answer_evaluable"]]
    ne = len(evaluable) or 1
    durations = sorted(latencies)
    summary = {
        "questions": len(results),
        "scope": args.scope,
        "corpus_references": sum(len(item[0]) for item in corpora.values()),
        "reference_hit_at_1": sum(
            1 for row in results if row["reference_rank"] == 1
        )
        / n,
        "reference_hit_at_5": sum(row["reference_hit"] for row in results) / n,
        "answer_supported_top1": sum(row["answer_supported_top1"] for row in results)
        / n,
        "answer_supported_at_5": sum(row["answer_supported_at_5"] for row in results)
        / n,
        "answer_evaluable_questions": len(evaluable),
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
        "scope": f"reference retrieval ({args.scope} scope); reasoning rows excluded unless --include-reasoning",
        "rerank": args.rerank,
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
