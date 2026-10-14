"""Publish a complete-set budget comparison without treating partial checkpoints as baselines."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.evaluate_semantic_set import evaluate


def _gold_hash(gold: dict) -> str:
    return hashlib.sha256(json.dumps(gold, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _complete_rows(gold: dict, rows: list[dict]) -> None:
    expected = {item["id"]: item for item in gold["items"]}
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Results must be captured row objects")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(item_id, str) for item_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("Result IDs must be unique strings")
    if set(ids) != set(expected):
        raise ValueError("A published comparison requires the complete frozen question set")
    for row in rows:
        item = expected[row["id"]]
        if any(row.get(name) != item[name] for name in ("question", "ticker", "category", "expected_behavior")):
            raise ValueError("Results do not match the frozen questions")


def _summary(gold: dict, rows: list[dict], report: dict) -> dict:
    metrics = evaluate(gold, rows)
    excluded = set(metrics["short_answer_excluded"])
    adequate = sum(row["status"] == "answered" and row["id"] not in excluded for row in rows)
    categories = {}
    for category in sorted({item["category"] for item in gold["items"]}):
        subset = [row for row in rows if row["category"] == category]
        categories[category] = {
            "questions": len(subset),
            "statuses": dict(Counter(row["status"] for row in subset)),
            "adequate_answer_count": sum(row["status"] == "answered" and row["id"] not in excluded for row in subset),
            "timeout_count": sum(bool(row.get("budget_timeout")) for row in subset),
        }
    return {
        "run_configuration": report["run_configuration"],
        "research_model": report["research_model"],
        "review_model": report["review_model"],
        "statuses": dict(Counter(row["status"] for row in rows)),
        "adequate_answer_count": adequate,
        "coverage_all_questions": round(adequate / len(gold["items"]), 6),
        "short_answer_details": [
            {"id": row["id"], "question": row["question"], "answer_chars": len(str(row.get("answer", ""))),
             "minimum_answer_chars": next(item.get("minimum_answer_chars", 40) for item in gold["items"] if item["id"] == row["id"])}
            for row in rows if row["id"] in excluded
        ],
        "metrics": metrics,
        "categories": categories,
    }


def compare(gold: dict, tight_rows: list[dict], tight_report: dict, production_rows: list[dict], production_report: dict) -> dict:
    _complete_rows(gold, tight_rows)
    _complete_rows(gold, production_rows)
    if production_report.get("status") != "scored":
        raise ValueError("Production report is not complete")
    config = production_report.get("run_configuration", {})
    if config.get("research_timeout_seconds") != 240 or config.get("model_timeout_seconds") != 60:
        raise ValueError("Production comparison requires the declared 240/60-second budgets")
    if not production_report.get("review_model") or production_report["review_model"] == production_report.get("research_model"):
        raise ValueError("Production evaluation requires a different review model")
    context = production_report.get("run_context", {})
    if context.get("gold_sha256") != _gold_hash(gold):
        raise ValueError("Production report belongs to a different frozen question set")
    if not context.get("fingerprint") or any(row.get("evaluation_fingerprint") != context["fingerprint"] for row in production_rows):
        raise ValueError("Production rows lack matching configuration provenance")
    tight = _summary(gold, tight_rows, tight_report)
    production = _summary(gold, production_rows, production_report)
    return {
        "schema": "filingcopilot.semantic-budget-comparison/v1",
        "status": "complete",
        "gold_sha256": _gold_hash(gold),
        "question_count": len(gold["items"]),
        "tight_budget": tight,
        "production_budget": production,
        "differences": {
            "adequate_answer_count": production["adequate_answer_count"] - tight["adequate_answer_count"],
            "coverage_all_questions_percentage_points": round(100 * (production["coverage_all_questions"] - tight["coverage_all_questions"]), 4),
            "timeout_rows": production["metrics"]["timeout_row_count"] - tight["metrics"]["timeout_row_count"],
        },
        "notes": [
            "The legacy tight-budget run mixes 20-second base rows and 30-second diagnostic replays; it is not homogeneous.",
            "Tight-budget rows are separately rescored from saved answers and explicit deadline gaps; original reports are preserved.",
            "coverage_all_questions keeps all questions in the denominator, including short answered rows, which receive no credit.",
            "The legacy coverage_rate excludes short rows from its denominator and is provided separately for compatibility.",
            "The frozen character minimum is a mechanical guard, not human-rated completeness; concise deterministic ratio outputs can be excluded. Their IDs and lengths are retained, without padding answers or changing the frozen set.",
            "Evidence-ID binding and recorded numeric verifier failures are mechanical checks, not human-verified truth or entailment.",
            "As-of restrictions are shared, but runtime corpus refresh, model variability and environment differences prevent a causal budget-only claim.",
            "Human semantic citation and review miss rates remain unknown until the separate blind labels are completed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("evaluation/semantic_questions_v1.json"))
    parser.add_argument("--tight-results", type=Path, required=True)
    parser.add_argument("--tight-report", type=Path, required=True)
    parser.add_argument("--production-results", type=Path, required=True)
    parser.add_argument("--production-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def read(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    report = compare(read(args.gold), read(args.tight_results), read(args.tight_report),
                     read(args.production_results), read(args.production_report))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "differences": report["differences"]}))


if __name__ == "__main__":
    main()
