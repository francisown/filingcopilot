import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package
from filingcopilot.localization import (
    current_language,
    header_language,
    language_scope,
    localized_prompt,
)
from filingcopilot.models import ResearchAnswer, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


@pytest.fixture
def local_service(tmp_path):
    # Bundled immutable SEC demo, FTS only, no provider or model requests.
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)


@pytest.mark.parametrize("header,expected", [
    (None, "en"), ("en-GB,en;q=0.9", "en"), ("zh-CN,en;q=0.3", "zh"),
    ("en;q=0,zh;q=0.8", "zh"), ("zh;q=0.2,en;q=0.9", "en"),
    ("fr,en;q=bad", "en"),
])
def test_header_language_negotiation(header, expected):
    assert header_language(header) == expected


def test_scope_restores_after_failure_and_isolates_workers():
    def worker(language):
        with language_scope(language):
            assert current_language() == language
            return current_language()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker, ["en", "zh"] * 8)) == ["en", "zh"] * 8
    with pytest.raises(RuntimeError), language_scope("en"):
        raise RuntimeError("test")
    assert current_language() == "zh"


def test_english_numeric_package_can_be_recomputed_without_rewriting_evidence(local_service):
    request = dict(question="FY2025 revenue and net income", ticker="AAPL", as_of=date(2026, 10, 8), mode="reference")
    chinese = local_service.research(ResearchRequest(**request, language="zh"))
    english = local_service.research(ResearchRequest(**request))
    assert english.language == "en" and chinese.language == "zh"
    assert english.answer and chinese.answer
    assert [c["evidence_ids"] for c in english.answer] == [c["evidence_ids"] for c in chinese.answer]
    assert english.evidence == chinese.evidence
    assert "Revenue" in english.answer[0]["text"] or any("Revenue" in c["text"] for c in english.answer)
    assert all("Reporting period" in c["text"] for c in english.answer if c["kind"] == "fact")
    package = local_service.export_package(english.id)
    before = json.dumps(package, sort_keys=True)
    assert audit_package(package)["status"] == "consistent"
    assert json.dumps(package, sort_keys=True) == before
    assert audit_package(local_service.export_package(chinese.id))["status"] == "consistent"


def test_api_language_header_and_explicit_override(local_service):
    body = {"question": "FY2025 revenue", "ticker": "AAPL", "as_of": "2026-10-08", "mode": "reference"}
    with TestClient(create_app(local_service)) as client:
        english = client.post("/api/research", json=body, headers={"Accept-Language": "en-US"})
        assert english.status_code == 200 and english.json()["language"] == "en"
        default = client.post("/api/research", json=body)
        assert default.status_code == 200 and default.json()["language"] == "en"
        chinese = client.post("/api/research", json={**body, "language": "zh"}, headers={"Accept-Language": "en"})
        assert chinese.status_code == 200 and chinese.json()["language"] == "zh"
        invalid = client.post("/api/research", json={**body, "language": "invalid"})
        assert invalid.status_code == 422


@pytest.mark.parametrize("option,expected", [(None, "en"), ("zh", "zh")])
def test_cli_new_research_defaults_to_english_and_can_select_chinese(local_service, monkeypatch, capsys, option, expected):
    from filingcopilot import cli

    monkeypatch.setattr(cli, "FilingCopilot", lambda _: local_service)
    argv = ["filingcopilot", "ask", "FY2025 revenue", "--ticker", "AAPL",
            "--as-of", "2026-10-08", "--mode", "reference"]
    if option:
        argv += ["--language", option]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["language"] == expected and output["usage"]["model_calls"] == 0
    label = "Revenue" if expected == "en" else "营业收入"
    assert any(label in c["text"] for c in output["answer"])


def test_old_answer_defaults_to_chinese_without_mutating_raw_payload():
    raw = dict(id="old",question="old",as_of="2026-10-08",mode="reference",status="abstained",
               answer=[],gaps=[],evidence=[],trace=[],usage={},elapsed_ms=0)
    assert ResearchAnswer.model_validate(raw).language == "zh"
    assert "language" not in raw


def test_prompt_preserves_checks_and_switches_the_full_language_directive():
    prompt = "Answer in Chinese. Summarize in Chinese within 120 Chinese characters. Never invent evidence IDs."
    english = localized_prompt(prompt, "en")
    assert "Answer in English" in english and "80 English words" in english
    assert "Never invent evidence IDs" in english
    assert localized_prompt(prompt, "zh") == prompt
