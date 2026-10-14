from datetime import date

import pytest
from test_cashflow import cash_fixture

from filingcopilot.audit import audit_package, create_package
from filingcopilot.models import FollowupRequest, ResearchAnswer
from filingcopilot.verification import render_numeric

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def saved_answer(service, run_id="run-before"):
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_financial_fact("AAPL", "operating_cash_flow", 2026)
    assert result["found"]
    evidence_id = result["evidence"]["id"]
    item = service.corpus.evidence(evidence_id, date(2026, 10, 8))
    claim = {"kind": "fact", "evidence_ids": [evidence_id]}
    claim.update({"index": 0, "text": render_numeric(item, tools.domain), "verification": "deterministic"})
    answer = ResearchAnswer(
        id=run_id,
        question="苹果 FY2026 经营现金流是多少？",
        as_of=date(2026, 10, 8),
        mode="reference",
        status="answered",
        answer=[claim],
        gaps=[],
        evidence=[item],
        trace=[],
        usage={"model_calls": 0, "tool_calls": 0},
        elapsed_ms=1,
    )
    service.corpus.save_run(
        answer.model_dump(mode="json"), create_package(answer, service.refresh_domain(), "AAPL")
    )
    return answer


def test_followup_reuses_ids_and_audits_without_reacquiring(tmp_path, monkeypatch):
    service, _ = cash_fixture(tmp_path)
    previous = saved_answer(service)
    monkeypatch.setattr(service.discovery, "search", lambda *_a, **_k: pytest.fail("refresh called"))
    followup = service.followup(
        FollowupRequest(
            research_id=previous.id,
            question="为什么？",
            as_of=previous.as_of,
            mode="reference",
            refresh_sources=False,
        )
    )
    assert followup.mode == "followup"
    assert followup.followup["version_status"] == "unchanged"
    assert followup.followup["prior_evidence_ids"] == [previous.evidence[0]["id"]]
    assert followup.answer[0]["text"] == previous.answer[0]["text"]
    package = service.corpus.run_package(followup.id)
    report = audit_package(package)
    assert report["status"] == "consistent"


def test_followup_rejects_earlier_as_of(tmp_path):
    service, _ = cash_fixture(tmp_path)
    previous = saved_answer(service)
    with pytest.raises(ValueError, match="不能早于"):
        service.followup(
            FollowupRequest(
                research_id=previous.id,
                question="为什么？",
                as_of=date(2026, 1, 1),
                mode="reference",
                refresh_sources=False,
            )
        )


def test_three_followups_keep_prior_runs_immutable(tmp_path):
    service, _ = cash_fixture(tmp_path)
    first = saved_answer(service)
    second = service.followup(
        FollowupRequest(research_id=first.id, question="为什么？", as_of=first.as_of)
    )
    third = service.followup(
        FollowupRequest(research_id=second.id, question="换个角度看风险？", as_of=second.as_of)
    )
    fourth = service.followup(
        FollowupRequest(research_id=third.id, question="哪些证据仍然不足？", as_of=third.as_of)
    )
    assert [second.followup["round"], third.followup["round"], fourth.followup["round"]] == [1, 2, 3]
    assert second.followup["prior_evidence_ids"] == third.followup["prior_evidence_ids"]
    assert third.followup["prior_evidence_ids"] == fourth.followup["prior_evidence_ids"]
    assert service.corpus.run(first.id)["question"] == first.question
    assert fourth.followup["context_mode"] == "evidence_ids"
    assert fourth.followup["reused_prior"] is True
