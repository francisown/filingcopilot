"""Pin the latency summary to the frozen workflow report."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "summarize_latency.py"
RESULTS = ROOT / "docs" / "validation" / "live-full-deepseek-mimo-2026-11-02-final" / "results.json"
REPORT = ROOT / "docs" / "validation" / "live-full-deepseek-mimo-2026-11-02-final" / "report.json"


def _module():
    spec = importlib.util.spec_from_file_location("summarize_latency", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_matches_reported_quantiles() -> None:
    module = _module()
    rows = json.loads(RESULTS.read_text(encoding="utf-8"))
    metrics = json.loads(REPORT.read_text(encoding="utf-8"))["metrics"]
    summary = module.summarize(rows)
    assert summary["model_backed_rows"] == metrics["model_backed_questions"]
    assert summary["latency_ms"]["p50"] == metrics["model_backed_p50_latency_ms"]
    assert summary["latency_ms"]["p95"] == metrics["model_backed_p95_latency_ms"]
    assert summary["author_ms"]["p95"] == 209735.0
    assert summary["review_ms"]["p95"] == 781985.0


def test_review_share_is_the_dominant_stage() -> None:
    module = _module()
    rows = json.loads(RESULTS.read_text(encoding="utf-8"))
    summary = module.summarize(rows)
    assert summary["review_share_p50"] > 0.75
    assert summary["review_ms"]["p50"] > summary["author_ms"]["p50"]


def test_new_stage_fields_are_summarized_when_present() -> None:
    module = _module()
    rows = [
        {
            "model_calls": 1,
            "latency_ms": 1000,
            "trace_summary": {
                "author_ms": 100,
                "review_ms": 900,
                "review_calls": 2,
                "retry_wait_ms": 50,
            },
        }
    ]
    summary = module.summarize(rows)
    assert summary["review_calls"]["p50"] == 2
    assert summary["retry_wait_ms"]["p50"] == 50
