import json
from dataclasses import replace
from datetime import date

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.agent_review import ReviewMixin
from filingcopilot.audit import audit_package, create_package
from filingcopilot.execution import ResearchCancelled, ResearchControl, research_scope
from filingcopilot.fundamentals import (
    _required_slot_status,
    narrative_source_context,
    stock_source_context,
)
from filingcopilot.models import Filing, Passage, ResearchRequest, StatementFact
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def add_source(tools, id, ticker="AAPL", form="10-K", filed="2025-11-01", end="2025-09-27"):
    filing = Filing(
        id=id,
        ticker=ticker,
        company=ticker,
        cik="0000320193" if ticker == "AAPL" else "0000789019",
        accession=id,
        form=form,
        filed=filed,
        period_end=end,
        source_url="https://www.sec.gov/Archives/" + id.replace(":", "-") + ".htm",
        content_sha256="b" * 64,
    )
    passages = [
        Passage(
            id=id + ":operating",
            filing_id=id,
            ticker=ticker,
            section="Operating results",
            text="Operating income revenue growth sales business segments reflect customer demand.",
            source_url=filing.source_url,
            locator="original operating paragraph",
        ),
        Passage(
            id=id + ":cash",
            filing_id=id,
            ticker=ticker,
            section="Liquidity",
            text="Capital expenditures investments operating cash flow support capacity projects.",
            source_url=filing.source_url,
            locator="original cash paragraph",
        ),
        Passage(
            id=id + ":risk",
            filing_id=id,
            ticker=ticker,
            section="Risk Factors",
            text="Risk Factors: competition, demand, regulatory and economic risks could affect operations.",
            source_url=filing.source_url,
            locator="original risk paragraph",
        ),
    ]
    tools.corpus.ingest(filing, [], passages)
    return filing


@pytest.fixture
def sources(tools):
    annual = add_source(tools, "AAPL:annual")
    quarter = add_source(tools, "AAPL:quarter", form="10-Q", filed="2026-07-31", end="2026-06-27")
    add_source(
        tools, "MSFT:quarter", ticker="MSFT", form="10-Q", filed="2026-07-31", end="2026-06-30"
    )
    return tools, annual, quarter


def test_candidates_use_current_quarter_and_annual_risks_without_foreign_sources(sources):
    tools, annual, quarter = sources
    context, records = stock_source_context(
        tools, "AAPL", {"fiscal_year": 2025}, {"filing": {"id": quarter.id}}
    )
    assert [r["arguments"]["filing_id"] for r in records] == [quarter.id, quarter.id, annual.id]
    assert len(records) == 3 and all(r["arguments"]["k"] == 8 for r in records)
    assert all(r["arguments"]["lexical_only"] for r in records)
    assert len(context["evidence"]) <= 3
    assert context["found"] and not context["missing"]
    assert {e["ticker"] for e in context["evidence"]} == {"AAPL"}
    assert {e["form"] for e in context["evidence"]} == {"10-K", "10-Q"}
    assert all(e["id"] in tools.ledger and "filing" not in e for e in context["evidence"])
    assert all(e["text"] == tools.ledger[e["id"]]["text"] for e in context["evidence"])


@pytest.mark.parametrize(
    ("question", "category", "expected_purposes"),
    [
        ("苹果 FY2025 的监管、需求和集中度风险有哪些？", "risk", {"监管与需求风险", "客户集中风险", "供应与经营风险"}),
        ("Did Apple state that there was no material supply-chain impact in FY2025?", "negation", {"否定原文", "反向证据", "管理层表述"}),
        ("Why did Apple's revenue change in FY2025?", "causality", {"经营变化原因", "分部变化原因", "现金与费用原因"}),
    ],
)
def test_narrative_source_context_is_bound_to_requested_annual_filing(
    sources, question, category, expected_purposes
):
    tools, annual, quarter = sources
    context, records = narrative_source_context(tools, "AAPL", question)
    assert context["found"] and context["category"] == category
    assert context["filing"]["id"] == annual.id
    assert len(records) == 3
    assert {record["purpose"] for record in records} == expected_purposes
    assert all(record["arguments"]["filing_id"] == annual.id for record in records)
    assert all(record["arguments"]["lexical_only"] for record in records)
    assert all(item["filing_id"] == annual.id for item in context["evidence"])
    assert all(item["id"] in tools.ledger for item in context["evidence"])
    assert context["answerability"] in {"supported_candidate", "insufficient_candidate"}


def test_broad_risk_question_does_not_require_unrequested_concentration(sources):
    tools, annual, _ = sources
    context, records = narrative_source_context(
        tools, "AAPL", "Which risks should an analyst track in Apple's FY2025 filing?"
    )
    assert context["category"] == "risk"
    assert "集中度" not in context["evidence_slots"]
    assert not any(record["purpose"] == "客户集中风险" for record in records)
    assert all(record["arguments"]["filing_id"] == annual.id for record in records)


def test_concentration_prefetch_includes_filing_bound_structured_rows(sources):
    tools, annual, _ = sources
    tools.corpus.add_statement_facts(
        annual,
        [
            StatementFact(
                id="AAPL:customer-concentration",
                filing_id=annual.id,
                ticker="AAPL",
                statement="other",
                metric="customer_concentration",
                label="Major customer revenue",
                concept="custom:MajorCustomerRevenue",
                value="0.10",
                unit="xbrli:pure",
                fiscal_year=2025,
                end=annual.period_end,
                source_url=annual.source_url,
                locator="customer concentration row",
                dimensions={"customer": "major"},
                dimension_labels={"customer": "Major customer"},
            )
        ],
    )
    context, records = narrative_source_context(
        tools, "AAPL", "Which customer concentration risks did Apple disclose for FY2025?"
    )
    rows = context["structured_evidence"]
    assert rows and rows[0]["id"] == "AAPL:customer-concentration"
    assert rows[0]["filing_id"] == annual.id
    assert any(record["tool"] == "get_statement" for record in records)


def test_narrative_source_context_reports_the_scope_it_actually_scanned(sources):
    """The model must be told a full scan happened, or it hedges into a blocking gap."""
    tools, annual, _ = sources
    context, _ = narrative_source_context(
        tools, "AAPL", "Did Apple state that there was no material supply-chain impact in FY2025?"
    )
    scope = context["scan_scope"]
    assert context["full_filing_scan"] is True
    assert scope["passages_scanned"] == len(
        tools.corpus.passages(tools.as_of, "AAPL", filing_id=annual.id)
    )
    assert scope["passages_scanned"] == context["full_filing_passage_count"] > 0
    assert scope["filing_form"] == annual.form
    assert scope["period_end"] == str(annual.period_end)
    assert set(scope["slot_hits"]) <= {"否定", "反证"}
    assert "not a retrieval failure" in scope["zero_slot_meaning"]


def test_narrative_context_exposes_required_slot_status(sources):
    tools, annual, _ = sources
    context, _ = narrative_source_context(
        tools, "AAPL", "Did Apple state that there was no material supply-chain impact in FY2025?"
    )
    assert set(context["slot_status"]) == {"否定", "反证"}
    assert set(context["unresolved_slots"]) <= {"否定", "反证"}
    assert all(item["required"] and item["hits"] >= 0 for item in context["slot_status"].values())
    assert context["answerability"] == (
        "supported_candidate" if not context["unresolved_slots"] else "insufficient_candidate"
    )


def test_required_slot_register_does_not_treat_one_slot_as_complete():
    status, unresolved = _required_slot_status(
        [("cause", "", []), ("period", "", [])], {"cause": 2}
    )
    assert status["cause"]["status"] == "covered"
    assert status["period"]["status"] == "unresolved"
    assert unresolved == ["period"]


def test_narrative_scan_prioritizes_a_question_named_segment_acronym(tools):
    """Question anchors must beat unrelated table prose without issuer rules."""
    filing = add_source(tools, "AMZN:annual", ticker="AMZN", end="2025-09-27")
    tools.corpus.ingest(
        filing,
        [],
        [
            Passage(
                id=filing.id + ":aws",
                filing_id=filing.id,
                ticker=filing.ticker,
                section="Operating results",
                text="AWS sales increased primarily due to customer usage and pricing.",
                source_url=filing.source_url,
                locator="AWS causal paragraph",
            ),
            Passage(
                id=filing.id + ":policy",
                filing_id=filing.id,
                ticker=filing.ticker,
                section="Revenue recognition",
                text="Service revenue is recognized when performance obligations are satisfied.",
                source_url=filing.source_url,
                locator="policy paragraph",
            ),
        ],
    )
    context, _ = narrative_source_context(
        tools,
        "AMZN",
        "根据期末为2025-09-27的年报，AWS收入增长的主要原因是什么？",
    )
    assert any("AWS sales increased" in item["text"] for item in context["evidence"])


def test_narrative_source_context_is_bounded_and_does_not_fabricate_missing_filings(sources):
    tools, _, _ = sources
    context, records = narrative_source_context(
        tools, "AAPL", "Why did Apple's revenue change in FY2025?", max_searches=1
    )
    assert context["found"] and len(records) == 1
    context, records = narrative_source_context(
        tools, "AAPL", "Why did Apple's revenue change in FY2030?"
    )
    assert not context["found"] and records == []


def test_required_gap_triggers_one_exact_filing_followup_search(sources):
    tools, annual, _ = sources
    state = {
        "focus_ticker": "AAPL",
        "question": "Why did Apple's FY2025 revenue change?",
        "trace": [
            {
                "stage": "narrative_source_preparation",
                "result": {"filing": {"id": annual.id}},
            }
        ],
        "tools": tools,
        "tool_count": 0,
        "usage": {},
    }
    result = ReviewMixin._retrieve_gap_evidence(state, ["revenue growth sales"])
    assert result and result["arguments"]["filing_id"] == annual.id
    assert result["evidence"]
    assert state["usage"]["tool_calls"] == 1
    assert state["trace"][-1]["stage"] == "gap_evidence_retrieval"


@pytest.mark.parametrize("quarter_id", ["MSFT:quarter", "not-observed"])
def test_unavailable_or_foreign_quarter_cannot_change_company_source_scope(sources, quarter_id):
    tools, annual, _ = sources
    context, records = stock_source_context(
        tools, "AAPL", {"fiscal_year": 2025}, {"filing": {"id": quarter_id}}
    )
    assert context["found"]
    assert {r["arguments"]["filing_id"] for r in records} == {annual.id}


def test_historical_asof_excludes_future_quarter_and_requested_year_stays_bound(sources):
    tools, annual, quarter = sources
    tools.as_of = date(2026, 3, 31)
    context, records = stock_source_context(
        tools, "AAPL", {"fiscal_year": 2025}, {"filing": {"id": quarter.id}}
    )
    assert context["found"] and {r["arguments"]["filing_id"] for r in records} == {annual.id}
    tools.as_of = date(2025, 10, 30)
    context, records = stock_source_context(tools, "AAPL", {"fiscal_year": 2025})
    assert not context["found"] and not records
    tools.as_of = date(2026, 10, 8)
    context, records = stock_source_context(tools, "AAPL", {"fiscal_year": 2024})
    assert not context["found"] and not records


@pytest.mark.parametrize("budget,expected", [(0, 0), (1, 1), (2, 2), (20, 3)])
def test_search_budget_is_bounded_and_no_extra_unscoped_searches(sources, budget, expected):
    tools, _, quarter = sources
    context, records = stock_source_context(
        tools, "AAPL", {"fiscal_year": 2025}, {"filing": {"id": quarter.id}}, budget
    )
    assert context["searches_performed"] == expected == len(records)
    assert bool(context["evidence"]) is bool(expected)


def test_search_failure_keeps_other_candidates_and_does_not_disclose_error_message(sources):
    tools, _, quarter = sources
    original = tools.search_passages
    calls = []

    def search(**arguments):
        calls.append(arguments)
        if len(calls) == 1:
            raise OSError("private transport details")
        return original(**arguments)

    tools.search_passages = search
    context, records = stock_source_context(
        tools, "AAPL", {"fiscal_year": 2025}, {"filing": {"id": quarter.id}}
    )
    assert context["found"] and len(context["missing"]) == 1
    assert records[0]["result"]["error_type"] == "OSError"
    assert "private transport" not in json.dumps([context, records])


def test_same_source_candidate_is_not_duplicated_for_different_search_purposes(sources):
    tools, _, _ = sources
    original = tools.search_passages
    tools.search_passages = lambda **arguments: original(**(arguments | {"query": "competition"}))
    context, records = stock_source_context(tools, "AAPL", {"fiscal_year": 2025})
    assert len(records) == 3 and len(context["evidence"]) == 1
    assert context["evidence"][0]["purposes"] == ["经营驱动", "现金投入", "披露风险"]


def test_cancel_during_preparation_stops_before_any_further_source_search(sources):
    tools, _, _ = sources
    original = tools.search_passages
    control = ResearchControl()
    calls = []

    def search(**arguments):
        calls.append(arguments)
        result = original(**arguments)
        control.cancel()
        return result

    tools.search_passages = search
    with research_scope(control), pytest.raises(ResearchCancelled):
        stock_source_context(tools, "AAPL", {"fiscal_year": 2025})
    assert len(calls) == 1


class PreparedGateway:
    def __init__(self):
        self.context = None

    def complete(self, messages, schemas, review=False):
        if review:
            claims = json.loads(messages[1]["content"])["claims"]
            message = {
                "content": json.dumps(
                    {
                        "verdicts": [
                            {"index": c["index"], "supported": True, "reason": "Controlled review"}
                            for c in claims
                        ]
                    }
                )
            }
        else:
            request = json.loads(messages[1]["content"])
            self.context = request.get("source_overview")
            risk = (
                next(e for e in self.context["evidence"] if "披露风险" in e["purposes"])
                if self.context
                else request["annual_overview"]["evidence"][0]
            )
            statement = (
                "公司披露竞争与需求风险，需要结合经营表现和估值进一步判断。"
                if self.context
                else "已取得年度营收信息，当前估值基准仍需核验。"
            )
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "submit",
                        "type": "function",
                        "function": {
                            "name": "submit_answer",
                            "arguments": json.dumps(
                                {
                                    "claims": [
                                        {
                                            "kind": "analysis",
                                            "evidence_ids": [risk["id"]],
                                            "statement": statement,
                                        }
                                    ]
                                }
                            ),
                        },
                    }
                ],
            }
        return {"message": message, "usage": {"total_tokens": 10}}


def test_first_model_call_can_submit_prepared_source_and_frozen_package_audits(sources, tmp_path):
    tools, _, _ = sources
    gateway = PreparedGateway()
    answer = ResearchAgent(Settings(home=tmp_path, min_model_interval=0), gateway).run(
        ResearchRequest(question="苹果 FY2025 股票怎么样？", ticker="AAPL", as_of="2026-10-08"),
        tools,
    )
    assert gateway.context["found"] and answer.usage["model_calls"] == 2
    assert answer.answer[0]["kind"] == "analysis"
    assert answer.answer[0]["evidence_ids"][0] in {e["id"] for e in answer.evidence}
    assert all(e["form"] == "10-K" for e in gateway.context["evidence"])
    audit = audit_package(create_package(answer, tools.domain, "AAPL"))
    assert audit["status"] == "consistent"


def test_low_tool_budget_reserves_submission_instead_of_starting_optional_searches(
    sources, tmp_path
):
    tools, _, _ = sources
    gateway = PreparedGateway()
    answer = ResearchAgent(
        Settings(home=tmp_path, min_model_interval=0, max_tool_calls=4), gateway
    ).run(
        ResearchRequest(question="苹果 FY2025 股票怎么样？", ticker="AAPL", as_of="2026-10-08"),
        tools,
    )
    assert gateway.context is None
    assert answer.usage["model_calls"] == 2 and answer.answer[0]["kind"] == "analysis"
    assert not any(t["stage"] == "stock_source_preparation" for t in answer.trace)


def test_lexical_preparation_does_not_load_embeddings_and_default_still_tries_hybrid(sources):
    tools, annual, _ = sources
    tools.retriever.settings = replace(
        tools.retriever.settings, embedding_mode="local", rerank_mode="anchor"
    )
    attempts = []

    def broken_vectors(passages):
        attempts.append(len(passages))
        raise OSError("unavailable optional embedding model")

    tools.retriever._vectors = broken_vectors
    context, records = stock_source_context(tools, "AAPL", {"fiscal_year": 2025})
    assert context["found"] and not attempts
    assert all(r["result"]["backend"].startswith("fts5") for r in records)
    result = tools.search_passages("operating income", "AAPL", filing_id=annual.id)
    assert attempts and result["hits"] and result["embedding_unavailable"] == "OSError"


def test_newer_annual_period_takes_priority_over_older_quarter(sources):
    tools, _, quarter = sources
    annual = add_source(tools, "AAPL:new-annual", filed="2026-08-01", end="2026-07-31")
    _, records = stock_source_context(
        tools, "AAPL", {"fiscal_year": 2026}, {"filing": {"id": quarter.id}}
    )
    assert {r["arguments"]["filing_id"] for r in records} == {annual.id}


def test_missing_quarter_filing_uses_annual_without_crashing(sources):
    tools, annual, _ = sources
    context, records = stock_source_context(tools, "AAPL", {"fiscal_year": 2025}, {"filing": None})
    assert context["found"] and {r["arguments"]["filing_id"] for r in records} == {annual.id}


def test_management_discussion_beats_first_accounting_note_candidate(sources):
    tools, _, _ = sources
    original = tools.search_passages

    def search(**arguments):
        result = original(**arguments)
        hits = result["hits"]
        if arguments["query"].startswith("Risk"):
            return result
        assert len(hits) >= 2
        hits[0]["passage"]["section"] = "Financial statements: interest rate derivatives"
        hits[1]["passage"]["section"] = "Management’s Discu ssion and Analysis"
        return result

    tools.search_passages = search
    context, records = stock_source_context(tools, "AAPL", {"fiscal_year": 2025})
    assert context["found"] and len(context["evidence"]) <= 3
    for record in records[:2]:
        assert record["selection"]["method"] == "section_priority"
        assert record["selection"]["selected_ids"] == [record["result"]["hits"][1]["passage"]["id"]]


def test_prepared_searches_leave_agent_able_to_request_missing_source(sources, tmp_path):
    tools, _, _ = sources

    class FollowupGateway(PreparedGateway):
        requested = False
        search_available = False

        def complete(self, messages, schemas, review=False):
            if not review and not self.requested:
                self.requested = True
                self.search_available = any(
                    s["function"]["name"] == "search_passages" for s in schemas
                )
                if not self.search_available:
                    raise ValueError("Prepared searches removed the follow-up source tool")
                return {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "followup",
                                "type": "function",
                                "function": {
                                    "name": "search_passages",
                                    "arguments": json.dumps(
                                        {
                                            "query": "competition",
                                            "ticker": "AAPL",
                                            "k": 1,
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    "usage": {"total_tokens": 10},
                }
            return super().complete(messages, schemas, review)

    gateway = FollowupGateway()
    answer = ResearchAgent(Settings(home=tmp_path, min_model_interval=0), gateway).run(
        ResearchRequest(question="苹果 FY2025 股票怎么样？", ticker="AAPL", as_of="2026-10-08"),
        tools,
    )
    assert gateway.search_available
    searches = [t for t in answer.trace if t.get("tool") == "search_passages"]
    assert len(searches) == 4 and searches[-1]["result"]["hits"]
    assert answer.usage["model_calls"] == 3 and answer.answer[0]["kind"] == "analysis"
