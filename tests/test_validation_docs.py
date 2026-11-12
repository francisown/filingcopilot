"""Keep published validation numbers tied to the frozen report.

The README, evaluation page and case study repeat a few headline numbers. This
test fails when one copy drifts from the others or from the machine-readable
report, which is the failure mode an external reviewer found in the README.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = (
    ROOT
    / "docs"
    / "validation"
    / "live-full-deepseek-mimo-2026-11-02-final"
    / "report.json"
)
DOCS = [
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "docs" / "evaluation.md",
    ROOT / "docs" / "case-study.md",
    ROOT / "docs" / "case-study.zh-CN.md",
]


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_published_regression_counts_agree() -> None:
    found: dict[str, set[str]] = {}
    pattern = re.compile(r"\*\*([0-9][0-9,]*) (?:passed|tests|个测试)\*\*")
    for path in DOCS:
        values = set(pattern.findall(path.read_text(encoding="utf-8")))
        if values:
            found[path.relative_to(ROOT).as_posix()] = values
    assert found, "at least one validation page must publish the regression count"
    counts = set().union(*found.values())
    assert len(counts) == 1, f"regression counts disagree: {found}"


def test_published_latency_matches_report() -> None:
    metrics = _report()["metrics"]
    p50 = f"{metrics['model_backed_p50_latency_ms'] / 1000:.1f}"
    p95 = f"{metrics['model_backed_p95_latency_ms'] / 1000:.1f}"
    evaluation = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
    study = (ROOT / "docs" / "case-study.md").read_text(encoding="utf-8")
    assert p50 in evaluation and p95 in evaluation, (p50, p95)
    assert p50 in study and p95 in study, (p50, p95)


def test_token_total_matches_report() -> None:
    metrics = _report()["metrics"]
    tokens = f"{metrics['total_tokens']:,}"
    evaluation = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
    study = (ROOT / "docs" / "case-study.md").read_text(encoding="utf-8")
    assert tokens in evaluation and tokens in study, tokens
