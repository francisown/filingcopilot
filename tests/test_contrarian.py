from datetime import date

import pytest

from filingcopilot.audit import audit_package, create_package
from filingcopilot.models import Filing, Passage, ResearchAnswer
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def fixture_service(tmp_path, text, ticker="AAPL"):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    filing = Filing(
        id=ticker + ":contrarian",
        ticker=ticker,
        company="Apple Inc.",
        cik="0000320193",
        accession="contrarian",
        form="10-K",
        filed="2026-01-30",
        period_end="2025-09-27",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/contrarian.htm",
        content_sha256="a" * 64,
    )
    service.corpus.ingest(
        filing,
        [],
        [
            Passage(
                id="contrarian-passage",
                filing_id=filing.id,
                ticker=ticker,
                section="Risk Factors",
                text=text,
                source_url=filing.source_url,
                locator="contrarian fixture",
            )
        ],
    )
    return service


def answer(text="增长和需求改善"):
    return ResearchAnswer(
        id="run-contrarian",
        question="股票怎么样？",
        as_of=date(2026, 10, 8),
        mode="agent",
        status="answered",
        answer=[{"kind": "analysis", "text": text, "evidence_ids": [], "verification": "inference_review"}],
        gaps=[],
        evidence=[],
        trace=[],
        usage={"model_calls": 0},
        elapsed_ms=0,
        research_type="stock",
    )


def test_contrarian_found_is_exactly_cited(tmp_path):
    service = fixture_service(tmp_path, "Demand growth remains strong, but risk pressure from competition and impairment may reduce margins.")
    tools = service.tools(date(2026, 10, 8))
    result = answer()
    service._contrarian_pass(result, tools, "AAPL")
    assert result.contrarian["status"] == "found"
    assert result.contrarian["items"][0]["title"] == "反证"
    assert result.contrarian["items"][0]["verification"] == "exact_source_excerpt"
    assert result.contrarian["items"][0]["evidence_ids"] == ["contrarian-passage"]


def test_contrarian_empty_is_explicit(tmp_path):
    service = fixture_service(tmp_path, "Demand growth remains strong and resilient with no interruption.", ticker="ZZZZ")
    tools = service.tools(date(2026, 10, 8))
    result = answer()
    service._contrarian_pass(result, tools, "ZZZZ")
    assert result.contrarian["status"] == "not_found"
    assert "未检索到反证" in result.contrarian["note"]


def test_contrarian_is_frozen_in_package_and_offline_audited(tmp_path):
    service = fixture_service(
        tmp_path,
        "Demand growth remains strong, but risk pressure from competition and impairment may reduce margins.",
    )
    tools = service.tools(date(2026, 10, 8))
    result = answer()
    service._contrarian_pass(result, tools, "AAPL")
    item = result.contrarian["items"][0]
    result.answer = [
        {
            "index": 0,
            "kind": "excerpt",
            "text": item["text"],
            "evidence_ids": item["evidence_ids"],
            "verification": "exact_source_excerpt",
        }
    ]
    package = create_package(result, service.domain, "AAPL")
    report = audit_package(package)
    assert report["status"] == "consistent"
    assert any(c["code"] == "contrarian_evidence" and c["status"] == "pass" for c in report["checks"])
