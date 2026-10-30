"""Replay the real string-shaped verification trace at the runner boundary."""

import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from filingcopilot.settings import Settings
from scripts import run_semantic_evaluation as runner

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_string_verification_findings_do_not_discard_saved_answer():
    row = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "run_semantic_evaluation.py"))["_row"]
    result = SimpleNamespace(
        id="run-saved",
        status="partial",
        evidence=[{"id": "fact-observed"}],
        answer=[{"kind": "fact", "text": "AAPL revenue", "evidence_ids": ["fact-observed"]}],
        trace=[{"stage": "verify", "findings": ["claim 1: narrative needs text and passage-only evidence"]}],
        gaps=["Risk explanation was withheld"],
        usage={"model_calls": 2, "actual_cost_usd": 0.0123},
    )
    captured = row({"id": "q1", "category": "numeric", "ticker": "AAPL", "question": "Revenue?", "expected_behavior": "answer_with_typed_fact_or_calculation"}, result, 123)
    assert captured["run_id"] == "run-saved"
    assert captured["status"] == "partial"
    assert captured["answer"] == "AAPL revenue"
    assert captured["numeric_errors"] == 0
    assert captured["cost_usd"] == 0.0123


def _item():
    return {"id": "q1", "category": "numeric", "ticker": "AAPL", "question": "Revenue?",
            "expected_behavior": "answer_with_typed_fact_or_calculation"}


def _checkpoint(tmp_path):
    item = _item()
    gold = {"items": [item]}
    settings = Settings(home=tmp_path, model="research", review_model="review", api_key="secret-value", api_base="https://private-provider.example")
    context = runner._manifest(gold, settings, 4, None, "2026-10-08")
    path = tmp_path / "checkpoint.json"
    row = {**item, "status": "answered", "unsupported_claims": 0, "numeric_errors": 0,
           "citation_supported": True, "evaluation_fingerprint": context["fingerprint"]}
    runner._atomic_json(path, [row])
    runner._atomic_json(path.with_suffix(".json.manifest.json"), context)
    return item, gold, settings, context, path, row


def test_resume_accepts_only_matching_frozen_configuration(tmp_path):
    item, gold, settings, context, path, row = _checkpoint(tmp_path)
    assert runner._resume_rows(path, context, [item]) == [row]
    production = runner._manifest(gold, replace(settings, research_timeout=20), 4, None, "2026-10-08")
    with pytest.raises(ValueError, match="differs"):
        runner._resume_rows(path, production, [item])
    assert "secret-value" not in str(context)
    assert "https://private-provider.example" not in str(context)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "missing_provenance", "wrong_question", "incomplete"])
def test_resume_rejects_mixed_or_invalid_rows(tmp_path, mutation):
    item, _, _, context, path, row = _checkpoint(tmp_path)
    rows = [row]
    if mutation == "duplicate":
        rows.append(dict(row))
    elif mutation == "unknown":
        row["id"] = "other"
    elif mutation == "missing_provenance":
        row.pop("evaluation_fingerprint")
    elif mutation == "wrong_question":
        row["question"] = "Different question"
    else:
        row.pop("status")
    runner._atomic_json(path, rows)
    with pytest.raises(ValueError):
        runner._resume_rows(path, context, [item])


def test_resume_rejects_legacy_rows_without_manifest(tmp_path):
    path = tmp_path / "legacy.json"
    runner._atomic_json(path, [])
    with pytest.raises(ValueError, match="manifest"):
        runner._resume_rows(path, {}, [])


def test_atomic_checkpoint_failure_preserves_previous_record(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    runner._atomic_json(path, ["completed"])
    original = path.read_bytes()

    def fail_replace(*_):
        raise OSError("interrupted replacement")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    with pytest.raises(OSError):
        runner._atomic_json(path, ["new"])
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_error_capture_redacts_credentials_before_truncating(tmp_path):
    settings = Settings(home=tmp_path, api_key="private-secret", api_base="https://private-server.example")
    message = runner._safe_message(RuntimeError("private-secret at https://private-server.example"), settings)
    assert message == "[redacted] at [redacted]"


def test_runner_error_is_not_mislabeled_budget_timeout():
    result = SimpleNamespace(id="r1", status="abstained", evidence=[], answer=[], trace=[],
                             gaps=["runner exception: connection failed"], usage={})
    assert runner._row(_item(), result, 10)["budget_timeout"] is False
    result.usage = {"budget_status": "expired"}
    assert runner._row(_item(), result, 10)["budget_timeout"] is True


def test_provider_failure_is_not_counted_as_a_wrong_refusal():
    """An infrastructure outage must not be scored as the system refusing."""
    result = SimpleNamespace(
        id="r2", status="abstained", evidence=[], answer=[], trace=[],
        gaps=["Model request failed and research was aborted; verified content is retained."],
        usage={},
    )
    row = runner._row(_item(), result, 10)
    assert row["wrong_refusal"] is False
    assert row["budget_timeout"] is False


def test_runner_passes_gold_output_language_to_research(monkeypatch, tmp_path):
    seen = []

    class FakeService:
        def __init__(self, _settings):
            pass

        def research(self, request):
            seen.append(request.language)
            return SimpleNamespace(
                id="r-language",
                status="answered",
                evidence=[],
                answer=[],
                trace=[],
                gaps=[],
                usage={},
            )

    monkeypatch.setattr(runner, "FilingCopilot", FakeService)
    settings = Settings(
        home=tmp_path,
        model="research",
        review_model="review",
        api_key="secret-value",
        api_base="https://private-provider.example",
    )
    gold = {
        "items": [
            {
                **_item(),
                "language": "zh",
            }
        ]
    }
    rows, _ = runner.run(gold, settings=settings, workers=1)
    assert rows[0]["status"] == "answered"
    assert seen == ["zh"]
