import json
from dataclasses import replace
from datetime import date

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.audit import audit_package, create_package
from filingcopilot.execution import ResearchCancelled, ResearchControl, research_scope
from filingcopilot.fundamentals import stock_source_context
from filingcopilot.models import Filing, Passage, ResearchRequest
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
    assert all(r["result"]["backend"] == "fts5" for r in records)
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
