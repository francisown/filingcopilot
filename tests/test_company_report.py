import copy
from datetime import date

import pytest

from filingcopilot.audit import audit_package
from filingcopilot.company_report import SECTIONS, ReportDraft, validate_report_layout
from filingcopilot.market import MarketError
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_full_report_offline_reuses_verification_and_package(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market.history = lambda *a, **k: (_ for _ in ()).throw(MarketError("offline fixture"))
    request = ResearchRequest(
        question="完整公司股票研究", ticker="AAPL", mode="reference", depth="full"
    )
    report = service.research(request)
    layout = next(t["sections"] for t in report.trace if t["stage"] == "company_report_layout")
    validate_report_layout(layout, report.answer)
    assert set(s["key"] for s in layout) == set(SECTIONS)
    assert any(s["status"] == "source_material" for s in layout)
    assert not any(c["kind"] == "analysis" for c in report.answer)
    assert report.usage["model_calls"] == 0
    assert audit_package(service.export_package(report.id))["status"] == "consistent"
    bad = copy.deepcopy(layout)
    active = next(s for s in bad if s["claim_indexes"])
    active["status"] = "reviewed"
    with pytest.raises(ValueError, match="coverage status"):
        validate_report_layout(bad, report.answer)


def test_report_requires_distinct_sections_and_specific_empty_gaps():
    draft = {"sections": [{"key": k, "gap": "需要补充可读原文"} for k in SECTIONS]}
    ReportDraft.model_validate(draft)
    draft["sections"][-1]["key"] = "risk"
    with pytest.raises(ValueError, match="exactly once"):
        ReportDraft.model_validate(draft)
    draft["sections"][-1] = {"key": "valuation"}
    with pytest.raises(ValueError, match="specific"):
        ReportDraft.model_validate(draft)


def test_agent_report_context_is_serializable_and_missing_sections_stay_incomplete(tmp_path):
    import json

    class Gateway:
        def complete(self, messages, tools, review=False):
            context = json.loads(messages[1]["content"])
            assert isinstance(context["as_of"], str)
            return {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_company_report",
                                "arguments": json.dumps(
                                    {
                                        "sections": [
                                            {"key": k, "gap": "测试来源不足"}
                                            for k in context["requested_sections"]
                                        ]
                                    }
                                ),
                            }
                        }
                    ]
                }
            }

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=Gateway())
    service.market.history = lambda *a, **k: (_ for _ in ()).throw(MarketError("offline fixture"))
    report = service.research(
        ResearchRequest(question="完整公司股票研究", ticker="AAPL", depth="full")
    )
    layout = next(t["sections"] for t in report.trace if t["stage"] == "company_report_layout")
    assert all(s["status"] in ("incomplete", "source_material") for s in layout)
    assert all(s["gap"] for s in layout)
    assert not any(t["stage"] == "report_draft_error" for t in report.trace)


def test_full_report_plan_rejects_unsupported_market_and_future_date(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    assert not service.research_plan(ResearchRequest(question="公司研究", depth="full"))["ready"]
    assert not service.research_plan(
        ResearchRequest(question="公司研究", ticker="0700.HK", depth="full")
    )["ready"]
    assert not service.research_plan(
        ResearchRequest(question="公司研究", ticker="AAPL", depth="full", as_of=date(2099, 1, 1))
    )["ready"]


def test_report_context_deduplicates_and_keeps_full_source_and_comparison_dates(tmp_path):
    from filingcopilot.company_report import report_model_context

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    tools = service.tools(date.today())
    overview = tools.get_company_overview("AAPL")
    source = tools.search_passages("risk", ticker="AAPL", k=1, lexical_only=True)["hits"][0]
    original = tools.ledger[source["passage"]["id"]]
    projected = report_model_context(
        {"annual_overview": overview, "selected_sources": {"risk": [original, original]}}, tools
    )
    assert "evidence" not in projected["prepared"]["annual_overview"]
    assert projected["observed_evidence"][original["id"]]["text"] == original["text"]
    assert len(projected["prepared"]["selected_sources"]["risk"]) == 2
    key = overview["comparisons"][0]["evidence_ids"][0]
    assert projected["observed_evidence"][key]["comparison_periods"]["inputs"][0]["start"]
