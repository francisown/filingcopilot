"""Resilience regressions: bounded retries, failover and review independence."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import httpx
import pytest
from openai import APIStatusError
from test_gateway_resilience import TOOLS, gateway, response

import filingcopilot.agent as agent_module
from filingcopilot.agent import ResearchAgent
from filingcopilot.agent_execution import ExecutionMixin
from filingcopilot.execution import ResearchControl, current_control, research_scope
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("transient_failures", [0, 2])
def test_capability_negotiation_on_last_attempt(tmp_path, transient_failures):
    choices = []

    def handler(request):
        choice = json.loads(request.read())["tool_choice"]
        choices.append(choice)
        if len(choices) <= transient_failures:
            return httpx.Response(500, json={"error": {"message": "temporary"}})
        if choice == "required":
            return httpx.Response(400, json={"error": {"message": "Unsupported tool_choice"}})
        return response()

    result = gateway(tmp_path, handler, model_attempts=transient_failures + 1).complete([], TOOLS)
    assert choices == ["required"] * (transient_failures + 1) + ["auto"]
    assert result["attempts"] == len(choices)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_error_does_not_switch_models(tmp_path, status):
    models = []

    def handler(request):
        models.append(json.loads(request.read())["model"])
        return httpx.Response(status, json={"error": {"message": "invalid request"}})

    with pytest.raises(APIStatusError):
        gateway(tmp_path, handler, review_model="judge").complete([], TOOLS)
    assert models == ["test"]


@pytest.mark.parametrize("concurrent", [False, True])
def test_shared_gateway_does_not_mix_research_authors(tmp_path, concurrent):
    barrier = Barrier(2) if concurrent else None

    def handler(request):
        body = json.loads(request.read())
        if body["messages"][0]["content"] == "failover" and body["model"] == "test":
            return httpx.Response(500, json={"error": {"message": "temporary"}})
        return response(body["model"])

    client = gateway(tmp_path, handler, review_model="judge", model_attempts=1)

    def run(failover):
        with research_scope(ResearchControl()):
            author = client.complete(
                [{"role": "user", "content": "failover" if failover else "normal"}], None
            )
            if barrier:
                barrier.wait(timeout=5)
            review = client.complete([{"role": "user", "content": "review"}], None, review=True)
            return author, review

    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [True, False]))
    else:
        results = [run(True), run(False)]
    for author, review in results:
        assert author["model"] != review["model"]
        assert "review_independence" not in review


class SilentGateway:
    def complete(self, messages, schemas, review=False):
        assert current_control() is not None
        if review:
            claims = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": c["index"],
                                    "supported": True,
                                    "reason": "Fixture approval",
                                }
                                for c in claims
                            ]
                        }
                    )
                },
                "model": "fake",
                "review_independence": "not_independent",
            }
        return {"message": {"role": "assistant", "content": "No submission"}, "model": "fake"}


def silent_answer(tools, question, **kwargs):
    return ResearchAgent(
        Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=4, min_model_interval=0),
        SilentGateway(),
    ).run(
        ResearchRequest(
            question=question, ticker="AAPL", as_of=tools.as_of, language="zh", **kwargs
        ),
        tools,
    )


@pytest.mark.parametrize(
    "question,operation",
    [
        ("AAPL FY2024 和 FY2025 营业收入分别是多少？", None),
        ("AAPL FY2025 营业收入与毛利率分别是多少？", None),
        ("请解释 AAPL FY2025 营业收入的主要驱动因素。", None),
        ("AAPL FY2025 revenue and its implications?", None),
        ("AAPL 和 MSFT FY2025 营业收入分别是多少？", None),
    ],
)
def test_numeric_fallback_cannot_hide_unanswered_parts(tools, question, operation):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    if operation:
        tools.get_financial_fact("AAPL", "revenue", 2024)
        tools.calculate(operation, "rev25", "rev24")
    answer = silent_answer(tools, question)
    assert answer.answer  # Useful checked figures are still delivered.
    assert answer.status == "partial"
    assert answer.gaps


def test_complete_numeric_fallback_keeps_answered_status(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    answer = silent_answer(tools, "AAPL FY2025 营业收入是多少？")
    assert answer.status == "answered"
    assert not answer.gaps


def test_silent_model_can_still_deliver_growth_from_observed_periods(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    answer = silent_answer(tools, "比较 AAPL FY2025 与上一财年的收入增长，期间是否一致？")
    calculations = [e for e in answer.evidence if e["kind"] == "calculation"]
    assert any(
        e["operation"] == "growth" and e["input_ids"] == ["rev25", "rev24"] for e in calculations
    )
    assert answer.status == "partial"  # No manufactured period interpretation.
    assert any(t.get("stage") == "fallback_calculation" for t in answer.trace)


def test_silent_model_can_still_deliver_requested_margin(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "gross_profit", 2025)
    answer = silent_answer(tools, "AAPL FY2025 毛利率是多少？")
    assert answer.status == "answered"
    assert any(e.get("operation") == "ratio" for e in answer.evidence)


@pytest.mark.parametrize("operation,question", [
    ("growth", "比较 AAPL FY2025 与上一财年的收入增长，期间是否一致？"),
    ("ratio", "AAPL FY2025 毛利率是多少？请说明变化原因。"),
])
@pytest.mark.parametrize("already_calculated", [False, True])
@pytest.mark.parametrize("max_tool_calls", [1, 20])
def test_partial_submission_still_delivers_requested_observed_math(tools, operation, question, already_calculated, max_tool_calls):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    tools.get_financial_fact("AAPL", "gross_profit", 2025)
    left, right = ("rev25", "rev24") if operation == "growth" else ("gross25", "rev25")
    if already_calculated:
        tools.calculate(operation, left, right)

    class PartialGateway:
        def complete(self, messages, schemas, review=False):
            assert not review  # Typed evidence requires no model reviewer.
            return {"model": "fixture", "message": {"role": "assistant", "tool_calls": [{
                "id": "partial", "type": "function", "function": {
                    "name": "submit_answer",
                    "arguments": json.dumps({"claims": [{"kind": "fact", "evidence_ids": ["rev25"]}],
                                             "gaps": ["期间或原因解释尚未完成。", "未取得代码计算结果，无法给出同比或利润率。"]}),
                },
            }]}}

    answer = ResearchAgent(
        Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1,
                 max_tool_calls=max_tool_calls, min_model_interval=0),
        PartialGateway(),
    ).run(ResearchRequest(question=question, ticker="AAPL", as_of=tools.as_of, language="zh"), tools)
    calculations = [e for e in answer.evidence if e.get("operation") == operation]
    assert any(c["evidence_ids"] == ["rev25"] for c in answer.answer)
    assert answer.status == "partial" and any("期间或原因解释尚未完成。" in g for g in answer.gaps)
    assert answer.usage["tool_calls"] <= max_tool_calls
    if max_tool_calls == 1 and not already_calculated:
        assert not calculations
        assert "未取得代码计算结果，无法给出同比或利润率。" in answer.gaps
        return
    assert "未取得代码计算结果，无法给出同比或利润率。" not in answer.gaps
    assert any(g.startswith("补充计算前记录的说明") and "其余缺口仍保留" in g for g in answer.gaps)
    assert len(calculations) == 1
    assert calculations[0]["input_ids"] == [left, right]
    assert Decimal(calculations[0]["value"]) == (100 if operation == "growth" else 40)
    assert sum(c["kind"] == "calculation" for c in answer.answer) == 1
    assert sum(t.get("stage") == "fallback_calculation" for t in answer.trace) == (0 if already_calculated else 1)


def test_fallback_does_not_fetch_missing_comparison_input(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    answer = silent_answer(tools, "AAPL FY2025 营业收入同比增长率是多少？")
    assert answer.status == "partial"
    assert not any(e["kind"] == "calculation" for e in answer.evidence)
    assert "rev24" not in tools.ledger


@pytest.mark.parametrize(
    "question", ["AAPL FY2025 营业收入同比增长率是多少？", "AAPL FY2025 revenue growth?"]
)
def test_difference_only_becomes_complete_after_verified_growth_is_created(tools, question):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    tools.calculate("difference", "rev25", "rev24")
    fallback = ExecutionMixin._observed_typed_fallback(tools, question, "AAPL")
    assert not ExecutionMixin._fallback_covers_question(tools, question, fallback)
    answer = silent_answer(tools, question)
    assert answer.status == "answered"
    growth = next(e for e in answer.evidence if e.get("operation") == "growth")
    assert growth["input_ids"] == ["rev25", "rev24"]
    assert growth["value"] == "100"
    assert growth["unit"] == "%"


@pytest.mark.parametrize("condition", ["budget", "ambiguous", "period"])
def test_observed_calculation_preserves_budget_version_and_period_guards(tools, condition):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    if condition == "ambiguous":
        tools.ledger["other-version"] = {
            **tools.ledger["rev24"],
            "id": "other-version",
            "value": "99",
        }
    if condition == "period":
        tools.ledger["rev24"].update(start="2024-10-01", end="2024-12-31")
    state = {
        "tools": tools,
        "question": "AAPL FY2025 营业收入同比增长率是多少？",
        "focus_ticker": "AAPL",
        "tool_count": 1 if condition == "budget" else 0,
        "usage": {},
        "trace": [],
    }
    ExecutionMixin._complete_observed_calculations(state, 1)
    assert not any(e.get("operation") == "growth" for e in tools.ledger.values())
    assert state["tool_count"] <= 1
    if condition == "period":
        assert state["trace"][0].get("error")


def test_growth_request_without_named_metric_uses_the_answered_metric(tools):
    """Completeness asks for growth the question never named; use the delivered fact."""
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    state = {
        "tools": tools,
        "question": "Explain Apple's FY2025 growth in the filing.",
        "focus_ticker": "AAPL",
        "tool_count": 0,
        "usage": {},
        "trace": [],
        "verified": [{"kind": "fact", "evidence_ids": ["rev25"]}],
    }
    ids = ExecutionMixin._complete_observed_calculations(state, 4)
    growth = [e for e in tools.ledger.values() if e.get("operation") == "growth"]
    assert ids and len(growth) == 1
    assert growth[0]["value"] == "100"
    assert growth[0]["unit"] == "%"


def test_agreeing_duplicate_observations_do_not_block_requested_growth(tools):
    """The guard exists for competing values, not for a twice-stored identical fact."""
    tools.get_financial_fact("AAPL", "revenue", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2024)
    tools.ledger["rev24-copy"] = {**tools.ledger["rev24"], "id": "rev24-copy"}
    state = {
        "tools": tools,
        "question": "AAPL FY2025 营业收入同比增长率是多少？",
        "focus_ticker": "AAPL",
        "tool_count": 0,
        "usage": {},
        "trace": [],
    }
    ExecutionMixin._complete_observed_calculations(state, 4)
    growth = [e for e in tools.ledger.values() if e.get("operation") == "growth"]
    assert len(growth) == 1
    assert growth[0]["value"] == "100"
    assert growth[0]["unit"] == "%"


def test_service_fallback_preserves_explanation_gap_in_english(tools):
    class ObserveThenSilent(SilentGateway):
        calls = 0

        def complete(self, messages, schemas, review=False):
            self.calls += 1
            if self.calls == 1:
                return {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "observe",
                                "type": "function",
                                "function": {
                                    "name": "get_financial_fact",
                                    "arguments": json.dumps(
                                        {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025}
                                    ),
                                },
                            }
                        ],
                    },
                    "model": "fake",
                }
            return super().complete(messages, schemas, review)

    service = FilingCopilot(
        Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=4, min_model_interval=0),
        gateway=ObserveThenSilent(),
    )
    answer = service.research(
        ResearchRequest(
            question="Explain AAPL FY2025 revenue drivers.",
            ticker="AAPL",
            as_of=tools.as_of,
            mode="agent",
            language="en",
            auto_acquire=False,
        )
    )
    assert answer.status == "partial"
    assert answer.answer
    assert any("unverified explanations are withheld" in gap for gap in answer.gaps)


def test_capability_negotiation_does_not_restart_transient_retry_budget(tmp_path):
    choices = []

    def handler(request):
        choices.append(json.loads(request.read())["tool_choice"])
        if choices[-1] == "required":
            return httpx.Response(400, json={"error": {"message": "Unsupported tool_choice"}})
        return httpx.Response(500, json={"error": {"message": "temporary"}})

    with pytest.raises(APIStatusError) as error:
        gateway(tmp_path, handler, model_attempts=1).complete([], TOOLS)
    assert error.value.status_code == 500
    assert choices == ["required", "auto"]


def test_basic_stock_brief_preserves_independence_disclosure(tools, monkeypatch):
    monkeypatch.setattr(
        agent_module,
        "stock_trailing_context",
        lambda *a, **kw: {"found": True, "evidence": [], "missing": [], "warnings": []},
    )
    monkeypatch.setattr(
        agent_module,
        "basic_stock_submission",
        lambda *a, **kw: {
            "claims": [
                {
                    "kind": "analysis",
                    "evidence_ids": ["rev25"],
                    "statement": "已披露年度营收，为经营规模提供依据。",
                },
                {"kind": "fact", "evidence_ids": ["rev25"]},
            ],
            "gaps": [],
        },
    )
    answer = silent_answer(tools, "苹果股票怎么样？")
    assert any(c["kind"] == "analysis" for c in answer.answer)
    assert any("独立" in gap for gap in answer.gaps)
