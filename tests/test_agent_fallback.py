"""Deterministic typed-evidence fallback and its completeness check.

A run whose model never submits must still deliver figures it already observed
and verified.  ``_fallback_covers_question`` decides whether that delivery
actually answers the question, so a complete numeric answer is not reported as
partial merely because the model stayed silent.
"""

from datetime import date

from filingcopilot.agent_execution import ExecutionMixin
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.tools import ResearchTools


def observe(tools: ResearchTools, metric: str, year: int) -> str:
    """Pull a fact through the tool so it enters the observed ledger."""
    return tools.get_financial_fact("AAPL", metric, year)["evidence"]["id"]


def claim(*ids: str) -> dict:
    return {"evidence_ids": list(ids)}


def covers(tools: ResearchTools, question: str, *claims: dict) -> bool:
    return ExecutionMixin._fallback_covers_question(tools, question, list(claims))


def test_complete_numeric_answer_is_not_reported_partial(tools):
    rev25 = observe(tools, "revenue", 2025)
    assert covers(tools, "AAPL FY2025 营业收入是多少？", claim(rev25)) is True


def test_missing_metric_keeps_the_question_uncovered(tools):
    # The question asks for revenue; a gross-profit claim cannot satisfy it.
    gross25 = observe(tools, "gross_profit", 2025)
    assert covers(tools, "AAPL FY2025 营业收入是多少？", claim(gross25)) is False


def test_two_metric_question_needs_both_metrics(tools):
    rev25 = observe(tools, "revenue", 2025)
    gross25 = observe(tools, "gross_profit", 2025)
    question = "AAPL FY2025 营业收入和毛利润是多少？"
    assert covers(tools, question, claim(rev25)) is False
    assert covers(tools, question, claim(rev25, gross25)) is True


def test_growth_question_needs_an_observed_calculation(tools):
    rev25 = observe(tools, "revenue", 2025)
    rev24 = observe(tools, "revenue", 2024)
    question = "比较 AAPL FY2025 与上一财年的营业收入增长"
    assert covers(tools, question, claim(rev25, rev24)) is False
    growth = tools.calculate("growth", rev25, rev24)["evidence"]["id"]
    # A requested prior fiscal year is valid supporting output, whether shown
    # separately or bound inside the growth calculation. Unrequested years are
    # still rejected by the ordinary verifier.
    assert covers(tools, question, claim(rev25, growth)) is True
    assert covers(tools, question, claim(rev25, rev24, growth)) is True


def test_narrative_questions_are_never_reported_complete(tools):
    rev25 = observe(tools, "revenue", 2025)
    gross25 = observe(tools, "gross_profit", 2025)
    for question in (
        "What are the main risks Apple discloses?",
        "年报披露了哪些主要风险？",
        "Does the filing deny a demand decline?",
        "为什么营业收入发生变化？",
    ):
        assert covers(tools, question, claim(rev25, gross25)) is False


def test_unregistered_metric_question_is_not_claimed_complete(tools):
    rev25 = observe(tools, "revenue", 2025)
    gross25 = observe(tools, "gross_profit", 2025)
    assert covers(tools, "苹果 FY2025 毛利率是多少？", claim(rev25, gross25)) is False


def test_fallback_delivers_only_observed_focus_evidence(tools):
    rev25 = observe(tools, "revenue", 2025)
    selected = ExecutionMixin._observed_typed_fallback(
        tools, "AAPL FY2025 营业收入是多少？", "AAPL"
    )
    assert [c["evidence_ids"] for c in selected] == [[rev25]]


def test_fallback_without_focus_returns_nothing(tools):
    observe(tools, "revenue", 2025)
    assert ExecutionMixin._observed_typed_fallback(tools, "营业收入是多少？", None) == []


def test_fallback_ignores_unobserved_evidence(tmp_path):
    """Only evidence actually pulled into the ledger can be delivered."""
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off", min_model_interval=0))
    tools = service.tools(date.today())
    assert ExecutionMixin._observed_typed_fallback(tools, "AAPL 营业收入", "AAPL") == []


def test_fallback_respects_the_named_fiscal_year(tools):
    observe(tools, "revenue", 2025)
    observe(tools, "revenue", 2024)
    selected = ExecutionMixin._observed_typed_fallback(
        tools, "AAPL FY2025 营业收入是多少？", "AAPL"
    )
    assert len(selected) == 1
    assert selected[0]["evidence_ids"] == ["rev25"]
