"""Exercise the actual Agent message boundary, without a live model."""

import json
from copy import deepcopy

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import ResearchRequest
from filingcopilot.settings import Settings
from filingcopilot.tools import tool_schemas

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


class CaptureGateway:
    def complete(self, messages, schemas, review=False):
        self.messages = deepcopy(messages)
        self.schemas = deepcopy(schemas)
        raise RuntimeError("Controlled boundary capture; no LLM request")


@pytest.mark.parametrize(
    "question,primary",
    [
        ("股票怎么样？结合经营表现和价格分析。", "annual"),
        ("最新季度股票怎么样？", "quarter"),
        ("TTM 股票估值怎么样？", "ttm"),
    ],
)
@pytest.mark.parametrize("incomplete_refresh", [False, True])
def test_stock_generation_has_one_bounded_contract_and_keeps_dynamic_tools(
    tools, tmp_path, question, primary, incomplete_refresh
):
    if incomplete_refresh:
        tools.refresh_status = {"annual": {"checked": False, "warning": "Refresh incomplete"}}
    gateway = CaptureGateway()
    ResearchAgent(Settings(home=tmp_path), gateway).run(
        ResearchRequest(language="zh", question=question, ticker="AAPL", as_of="2026-10-08"), tools
    )
    system = gateway.messages[0]["content"]
    assert len(system) <= 7000, "Stock generation repeats instructions for other research flows"
    request = json.loads(gateway.messages[1]["content"])
    assert request["question"] == question
    assert request["requested_scope"]["primary"] == primary
    assert request["annual_overview"]["found"]
    if incomplete_refresh:
        assert request["source_refresh"] == tools.refresh_status
        assert "latest AVAILABLE" in system
    evidence = request["annual_overview"]["evidence"]
    assert evidence and all(e["id"] in tools.ledger for e in evidence)
    # Missing evidence must remain retrievable, including a different FY or metric.
    assert {s["function"]["name"] for s in gateway.schemas} == {
        s["function"]["name"] for s in tool_schemas(tools.domain, stock=True)
    }
    assert any(s["function"]["name"] == "search_passages" for s in gateway.schemas)


def test_stock_submission_schema_bounds_the_brief_without_changing_other_research(tools):
    stock = next(
        s["function"]
        for s in tool_schemas(tools.domain, stock=True)
        if s["function"]["name"] == "submit_answer"
    )["parameters"]["properties"]["claims"]
    assert stock["maxItems"] == 3
    assert stock["items"]["properties"]["kind"]["enum"] == ["analysis"]
    assert stock["items"]["properties"]["statement"]["maxLength"] == 120
    general = next(
        s["function"]
        for s in tool_schemas(tools.domain)
        if s["function"]["name"] == "submit_answer"
    )["parameters"]["properties"]["claims"]
    assert general["maxItems"] == 10
    assert {"fact", "calculation", "narrative", "analysis"} <= set(
        general["items"]["properties"]["kind"]["enum"]
    )


def test_focused_cash_question_keeps_its_original_generation_contract(tools, tmp_path):
    gateway = CaptureGateway()
    ResearchAgent(Settings(home=tmp_path), gateway).run(
        ResearchRequest(language="zh", question="自由现金流为什么下降？", ticker="AAPL", as_of="2026-10-08"),
        tools,
    )
    assert "For a focused cash-flow explanation" in gateway.messages[0]["content"]
    submit = next(s for s in gateway.schemas if s["function"]["name"] == "submit_answer")
    assert (
        "fact"
        in submit["function"]["parameters"]["properties"]["claims"]["items"]["properties"]["kind"][
            "enum"
        ]
    )
