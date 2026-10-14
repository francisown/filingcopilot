"""Mixed stock assessment and announcement requests use both research capabilities."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.audit import audit_package, create_package
from filingcopilot.models import Claim, Filing, Passage, ResearchRequest, Submission
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

QUESTION = "股票怎么样？结合经营表现、价格和近期公告，中文总结。"


class CaptureGateway:
    def complete(self, messages, schemas, review=False):
        self.messages = deepcopy(messages)
        self.schemas = deepcopy(schemas)
        raise RuntimeError("Controlled mixed request capture; no network")


def test_mixed_request_plan_prepares_stock_and_announcements(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.gateway = CaptureGateway()
    try:
        plan = service.research_plan(
            ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08")
        )
        assert plan["ready"] and plan["uses_model"]
        assert plan["kind"] == "stock", "Mixed request silently selects announcements alone"
        assert "公告" in plan["message"]
        reference = service.research_plan(
            ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08", mode="reference")
        )
        assert not reference["uses_model"] and "请切换 Agent" in reference["message"]
        assert "公告原文可单独查询" in reference["message"]
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_mixed_agent_can_submit_assessment_and_official_notice(tools, tmp_path):
    gateway = CaptureGateway()
    ResearchAgent(Settings(home=tmp_path), gateway).run(
        ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08"), tools
    )
    request = json.loads(gateway.messages[1]["content"])
    assert request["annual_overview"]["found"]
    assert "company_updates" in request
    submit = next(s for s in gateway.schemas if s["function"]["name"] == "submit_answer")
    claims = submit["function"]["parameters"]["properties"]["claims"]
    assert set(claims["items"]["properties"]["kind"]["enum"]) == {"analysis", "narrative"}
    assert claims["maxItems"] == 5
    assert "official company update query" not in gateway.messages[0]["content"]
    assert "needs BOTH parts" in gateway.messages[0]["content"]


@pytest.fixture
def mixed_tools(tools):
    for index, day in enumerate(("2026-10-07", "2026-09-18", "2026-09-10")):
        filing = Filing(
            id=f"AAPL:notice{index}",
            ticker="AAPL",
            company="Apple",
            cik="0000320193",
            accession=f"0000320193-26-00000{index}",
            form="8-K",
            filed=day,
            period_end=day,
            source_url=f"https://www.sec.gov/Archives/notice{index}.htm",
            content_sha256="b" * 64,
        )
        tools.corpus.ingest(
            filing,
            [],
            [
                Passage(
                    id=f"notice{index}",
                    filing_id=filing.id,
                    ticker="AAPL",
                    section="Announcement",
                    text="Apple announced financial results and discussed its services business.",
                    source_url=filing.source_url,
                    locator="narrative block 1",
                )
            ],
        )
    return tools


class MixedGateway:
    def __init__(self, *, notices=True, reject_notices=False, follow_up=False, mutation=None):
        self.notices = notices
        self.reject_notices = reject_notices
        self.follow_up = follow_up
        self.mutation = mutation
        self.calls = []

    def complete(self, messages, schemas, review=False):
        self.calls.append((deepcopy(messages), review))
        if review:
            claims = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": c["index"],
                                    "supported": not (
                                        self.reject_notices and c["kind"] == "narrative"
                                    ),
                                    "reason": "Controlled review; not a model quality evaluation",
                                }
                                for c in claims
                            ]
                        }
                    )
                },
                "usage": {"total_tokens": 10},
            }
        context = json.loads(messages[1]["content"])
        if self.follow_up and not any(m.get("role") == "tool" for m in messages):
            name, arguments = (
                "get_financial_fact",
                {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2024},
            )
        else:
            claims = [
                {
                    "kind": "analysis",
                    "statement": "已取得年度营收信息，仍需结合价格和盈利进一步判断。",
                    "evidence_ids": [context["annual_overview"]["evidence"][0]["id"]],
                }
            ]
            if self.notices:
                claims += [
                    {
                        "kind": "narrative",
                        "statement": "公司发布财务业绩，并介绍服务业务。",
                        "evidence_ids": event["evidence_ids"],
                    }
                    for event in context["company_updates"]["events"][:2]
                ]
            if self.mutation:
                self.mutation(claims, context)
            name, arguments = "submit_answer", {"claims": claims}
        return {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "mixed-call",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            },
            "usage": {"total_tokens": 10},
        }


def test_mixed_summary_keeps_dynamic_financial_calls_and_both_reviewed_parts(mixed_tools, tmp_path):
    gateway = MixedGateway(follow_up=True)
    result = ResearchAgent(Settings(home=tmp_path, min_model_interval=0), gateway).run(
        ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08"), mixed_tools
    )
    assert sum(c["kind"] == "analysis" for c in result.answer) == 1
    assert sum(c["kind"] == "narrative" for c in result.answer) == 2
    follow_up = next(t for t in result.trace if t.get("tool") == "get_financial_fact")
    assert follow_up["result"]["found"] and follow_up["arguments"]["fiscal_year"] == 2024
    assert not any(t["stage"] == "notice_excerpt_fallback" for t in result.trace)
    assert all(
        key in {e["id"] for e in result.evidence}
        for event in gateway.calls[0][0][1:2]
        for key in json.loads(event["content"])["company_updates"]["events"][2]["evidence_ids"]
    )
    package = create_package(result, domain=mixed_tools.domain, focus_ticker="AAPL")
    assert audit_package(package, corpus=mixed_tools.corpus)["checks_failed"] == 0


@pytest.mark.parametrize("reject", [False, True])
def test_missing_or_rejected_summaries_keep_matched_excerpts_and_a_concrete_next_step(
    mixed_tools, tmp_path, reject
):
    gateway = MixedGateway(notices=reject, reject_notices=reject)
    result = ResearchAgent(
        Settings(home=tmp_path, min_model_interval=0, max_rounds=2), gateway
    ).run(ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08"), mixed_tools)
    assert any(c["kind"] == "analysis" for c in result.answer)
    assert not any(c["kind"] == "narrative" for c in result.answer)
    excerpts = [c for c in result.answer if c["kind"] == "excerpt"]
    assert len(excerpts) == 2 and all(c["verification"] == "exact_source_excerpt" for c in excerpts)
    assert any("近期公司公告中文摘要" in gap for gap in result.gaps)
    package = create_package(result, domain=mixed_tools.domain, focus_ticker="AAPL")
    assert audit_package(package, corpus=mixed_tools.corpus)["checks_failed"] == 0


def test_unreadable_notices_keep_the_financial_assessment(tools, tmp_path):
    result = ResearchAgent(Settings(home=tmp_path), MixedGateway(notices=False)).run(
        ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08"), tools
    )
    assert any(c["kind"] == "analysis" for c in result.answer)
    assert any("近 90 天公告未取得可读原文" in gap for gap in result.gaps)
    assert not any(c["kind"] == "excerpt" for c in result.answer)


@pytest.mark.parametrize(
    "question",
    [
        QUESTION,
        "FY2025 股票怎么样？结合近期公告，中文总结。",
        "How is this stock? Include business, price and recent updates.",
    ],
)
def test_service_execution_prepares_every_part_of_the_mixed_plan(
    mixed_tools, tmp_path, monkeypatch, question
):
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", sec_user_agent="fixture contact@example.test")
    )
    service.settings = replace(service.settings, min_model_interval=0)
    gateway = MixedGateway()
    service.gateway = gateway
    calls = []

    def prepare(stage):
        def invoke(ticker, as_of):
            calls.append(stage)
            return {"ticker": ticker, "checked": True, "warnings": []}

        return invoke

    monkeypatch.setattr(service, "tools", lambda as_of: mixed_tools)
    for method in ("prepare", "refresh_annual", "prepare_quarterly", "prepare_updates"):
        monkeypatch.setattr(service.discovery, method, prepare(method))
    try:
        result = service.research(
            ResearchRequest(language="zh", question=question, ticker="AAPL", as_of="2026-10-08", auto_acquire=True)
        )
        assert calls == (
            ["prepare", "prepare_updates"]
            if "FY2025" in question
            else ["prepare", "refresh_annual", "prepare_quarterly", "prepare_updates"]
        )
        assert result.research_type == "stock"
        assert any(c["kind"] == "analysis" for c in result.answer)
        assert sum(c["kind"] == "narrative" for c in result.answer) == 2
        context = json.loads(gateway.calls[0][0][1]["content"])
        assert context["annual_overview"]["found"] and context["company_updates"]["found"]
        assert service.audit_run(result.id)["checks_failed"] == 0
    finally:
        service.discovery.executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda claims, context: claims.append(dict(claims[1])),
        lambda claims, context: claims[1].update(evidence_ids=["unobserved"]),
        lambda claims, context: claims[1].update(
            evidence_ids=context["company_updates"]["events"][2]["evidence_ids"]
        ),
        lambda claims, context: claims[1].update(evidence_ids=["rev25"]),
        lambda claims, context: claims[1].update(statement="公告" * 61),
        lambda claims, context: claims.extend([dict(claims[0])] * 3),
    ],
)
def test_mixed_submission_rejects_wrong_notice_scope_or_output_bounds(
    mixed_tools, tmp_path, mutation
):
    result = ResearchAgent(
        Settings(home=tmp_path, max_rounds=1), MixedGateway(mutation=mutation)
    ).run(ResearchRequest(language="zh", question=QUESTION, ticker="AAPL", as_of="2026-10-08"), mixed_tools)
    assert not any(c["kind"] == "narrative" for c in result.answer)
    assert any(
        t.get("tool") == "submit_answer" and t.get("result", {}).get("recoverable")
        for t in result.trace
    )
    assert sum(c["kind"] == "excerpt" for c in result.answer) == 2


@pytest.mark.parametrize(
    "filed,accepted",
    [
        ("2026-10-07", True),
        ("2026-07-11", True),
        ("2026-07-10", False),
        ("2026-10-09", False),
    ],
)
def test_mixed_notice_scope_is_public_date_window_not_the_financial_year(
    mixed_tools, filed, accepted
):
    overview = mixed_tools.get_company_updates("AAPL")
    key = overview["events"][0]["evidence_ids"][0]
    mixed_tools.ledger[key]["filing"]["filed"] = filed
    result, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="narrative",
                    statement="公司发布财务业绩，并介绍服务业务。",
                    evidence_ids=[key],
                )
            ]
        ),
        mixed_tools,
        "FY2025 股票怎么样？结合近期公告。",
        focus_ticker="AAPL",
        semantic_review=lambda q, claims: {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "Controlled policy test"}
                for c in claims
            ]
        },
    )
    assert bool(result) is accepted
    assert bool(errors) is not accepted


def test_mixed_notice_exception_does_not_relax_financial_year_checks(tools):
    tools.search_passages("manufacturing partners", ticker="AAPL", lexical_only=True)
    result, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", statement="供应中断可能影响经营。", evidence_ids=["p1"])]
        ),
        tools,
        "FY2024 股票怎么样？结合近期公告。",
        focus_ticker="AAPL",
    )
    assert not result and any("fiscal year" in error for error in errors)


@pytest.mark.parametrize(
    "statement,allowed",
    [
        ("公告未说明募资用途。", False),
        ("该公告中没有披露募集资金用途。", False),
        ("所引摘录未说明募资用途。", True),
    ],
)
def test_selected_notice_excerpt_cannot_prove_document_wide_absence(
    mixed_tools, statement, allowed
):
    key = mixed_tools.get_company_updates("AAPL")["events"][0]["evidence_ids"][0]
    result, errors = verify_submission(
        Submission(claims=[Claim(kind="narrative", statement=statement, evidence_ids=[key])]),
        mixed_tools,
        QUESTION,
        focus_ticker="AAPL",
        semantic_review=lambda q, claims: {
            "verdicts": [
                {
                    "index": c["index"],
                    "supported": True,
                    "reason": "Intentionally overconfident fixture reviewer",
                }
                for c in claims
            ]
        },
    )
    assert bool(result) is allowed
    assert bool(errors) is not allowed


@pytest.mark.parametrize("question", ["近期公司公告中文摘要", "最近有哪些公司公告？"])
def test_pure_notice_requests_keep_their_existing_plan(tmp_path, question):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        plan = service.research_plan(
            ResearchRequest(language="zh", question=question, ticker="AAPL", as_of="2026-10-08")
        )
        assert plan["kind"] == "updates" and plan["ready"]
    finally:
        service.discovery.executor.shutdown(wait=True)
