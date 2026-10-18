"""Cross-check delivered typed numbers against cached raw SEC companyfacts rows.

This is a source/value check, not a semantic accuracy score. It binds concept,
currency, period, accession and availability instead of matching a bag of numbers.
Unsupported native-filing/market evidence is reported as unassessed.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path


def check(item: dict, facts: dict, as_of: str) -> tuple[str, str]:
    if item.get("kind") == "fact":
        filing = item.get("filing", {})
        company = facts.get(str(int(filing.get("cik") or 0)))
        if company is None or ":" not in item.get("concept", ""):
            return "unassessed", "raw companyfacts unavailable"
        namespace, concept = item["concept"].split(":", 1)
        node = company.get("facts", {}).get(namespace, {}).get(concept)
        if node is None:
            return "unassessed", "concept is not represented in companyfacts"
        rows = node.get("units", {}).get(item["unit"], [])
        matched = [
            row
            for row in rows
            if (
                row.get("start") == item.get("start")
                and row.get("end") == item.get("end")
                and row.get("accn") == filing.get("accession")
                and row.get("filed")
                and row["filed"] <= as_of
                and Decimal(str(row["val"])) == Decimal(item["value"])
            )
        ]
        return ("matched", "exact SEC row") if matched else ("mismatch", "no exact SEC row")
    if item.get("kind") != "calculation":
        return "unassessed", "not a supported typed financial number"
    inputs = item.get("inputs", [])
    operation = item.get("operation")
    if operation not in {"growth", "ratio", "difference", "sum"} or len(inputs) != 2:
        return "unassessed", "calculation operation outside this checker"
    checks = [check(value, facts, as_of) for value in inputs]
    if any(status == "mismatch" for status, _ in checks):
        return "mismatch", "input does not match raw SEC row"
    if any(status != "matched" for status, _ in checks):
        return "unassessed", "input source is unassessed"
    left, right = inputs
    if left.get("ticker") != right.get("ticker") or left.get("unit") != right.get("unit"):
        return "mismatch", "incompatible company or unit"
    if operation == "ratio" and (left.get("start"), left.get("end")) != (
        right.get("start"),
        right.get("end"),
    ):
        return "mismatch", "ratio mixes periods"
    if operation in {"growth", "difference"} and left.get("metric") != right.get("metric"):
        return "mismatch", "change mixes metrics"
    with localcontext() as context:
        context.prec = 40
        a, b = Decimal(left["value"]), Decimal(right["value"])
        if operation in {"ratio", "growth"} and b == 0:
            return "mismatch", "zero denominator"
        expected = (
            a / b * 100
            if operation == "ratio"
            else (a - b) / b * 100
            if operation == "growth"
            else a - b
            if operation == "difference"
            else a + b
        )
        actual = Decimal(item["value"])
        if abs(actual - expected) > max(Decimal("1e-18"), abs(expected) * Decimal("1e-25")):
            return "mismatch", "arithmetic differs"
    expected_unit = "%" if operation in {"growth", "ratio"} else left["unit"]
    if item.get("unit") != expected_unit:
        return "mismatch", "wrong calculation unit"
    return "matched", "raw inputs and recomputed arithmetic"


def inspect_answers(directory: Path, cache: Path):
    facts = {}
    for path in cache.glob("*.cache"):
        with path.open("rb") as stream:
            prefix = stream.read(100)
        if b'"cik"' not in prefix:
            continue
        data = json.loads(path.read_bytes())
        if "facts" in data:
            facts[str(int(data["cik"]))] = data
    rows = []
    for path in sorted(directory.glob("*.answer.json")):
        answer = json.loads(path.read_text(encoding="utf-8"))
        evidence = {item["id"]: item for item in answer["evidence"]}
        for claim in answer["answer"]:
            if claim["kind"] not in {"fact", "calculation", "market", "valuation"}:
                continue
            for eid in claim["evidence_ids"]:
                status, reason = check(evidence[eid], facts, answer["as_of"])
                rows.append(
                    {
                        "question": path.name.removesuffix(".answer.json"),
                        "evidence_id": eid,
                        "status": status,
                        "reason": reason,
                    }
                )
    return {
        "scope": "Typed source/value check, not question completeness or narrative accuracy",
        "counts": dict(Counter(row["status"] for row in rows)),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_answers(args.answers, args.cache)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
