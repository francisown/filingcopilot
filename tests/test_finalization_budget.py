"""Exhausted evidence budgets must still permit bounded final submission."""
import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.settings import Settings


def execute_state(tools, name, arguments, count=2):
    return dict(question="What was revenue?", tools=tools, trace=[], rounds=1, tool_count=count,
                seen=set(), usage={}, errors=[], messages=[{"role": "assistant", "tool_calls": [{
                    "id": "call", "function": {"name": name, "arguments": json.dumps(arguments)}}]}])


def test_final_submission_survives_exhausted_research_budget(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    state = execute_state(tools, "submit_answer", {"claims": [{"kind": "fact", "evidence_ids": ["rev25"]}], "gaps": []})
    result = ResearchAgent(Settings(home=tools.corpus.home, max_tool_calls=2), object()).execute(state)
    assert result["submission"] is not None, result["trace"]
    assert result["action"] == "verify"
    assert result["tool_count"] <= 2


def test_budget_still_blocks_new_evidence_calls(tools, monkeypatch):
    monkeypatch.setattr(tools, "call", lambda *a, **kw: pytest.fail("No new tool IO after budget"))
    args = {"ticker": "AAPL"}
    state = execute_state(tools, "list_filings", args)
    result = ResearchAgent(Settings(home=tools.corpus.home, max_tool_calls=2), object()).execute(state)
    assert result["submission"] is None
    assert result["tool_count"] <= 2
    assert result["trace"][-1]["arguments"] == args, "Budget rejection must not erase what was requested"
    assert "limit" in result["trace"][-1]["result"]["error"]


def test_cache_hit_does_not_spend_evidence_budget(tools, monkeypatch):
    args = {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025}
    cached = tools.get_financial_fact(**args)
    key = json.dumps(["get_financial_fact", args], sort_keys=True)
    state = execute_state(tools, "get_financial_fact", args)
    state.update(seen={key}, tool_cache={key: cached})
    monkeypatch.setattr(tools, "call", lambda *a, **kw: pytest.fail("Cache hit must avoid IO"))
    result = ResearchAgent(Settings(home=tools.corpus.home, max_tool_calls=2), object()).execute(state)
    assert any(t["stage"] == "tool_cache_hit" for t in result["trace"])
    assert result["tool_count"] == 2


@pytest.mark.parametrize("question", ["Why did FY2025 margins change?", "What drove FY2025 revenue growth?"])
def test_narrative_prefetch_always_uses_text_queries(tools, monkeypatch, question):
    from filingcopilot.fundamentals import narrative_source_context
    queries = []
    def search(query, **kwargs):
        queries.append(query)
        return {"hits": []}
    monkeypatch.setattr(tools, "search_passages", search)
    narrative_source_context(tools, "AAPL", question)
    assert len(queries) == 3
    assert all(isinstance(q, str) and q.strip() for q in queries), queries
