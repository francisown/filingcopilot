"""Backend UI translation preserves dynamic amounts and historical claim text."""

import re

import pytest

from filingcopilot.localization import language_scope, legacy_numeric_text, prepare_answer, text
from filingcopilot.models import ResearchAnswer


@pytest.mark.parametrize("seconds", [0, 10, 120, 420, 1420, 120.0, 420.5])
def test_api_answer_gap_preserves_budget_and_verified_count(seconds):
    message = f"本轮预算 {seconds} 秒。保留了 10 项通过核验的结果；未完成审查的判断不作结论。"
    answer = ResearchAnswer(id="budget", question="question", as_of="2026-10-08",
                            mode="agent", status="abstained", answer=[], gaps=[message],
                            evidence=[], trace=[], usage={}, elapsed_ms=0)
    prepared = prepare_answer(answer, "en")
    assert re.findall(r"\d+(?:\.\d+)?", prepared.gaps[0]) == [str(seconds), "10"]
    assert not re.search(r"[\u4e00-\u9fff]", prepared.gaps[0])
    assert text(message, "zh") == message


@pytest.mark.parametrize("seconds", [10, 120, 420])
def test_dynamic_plan_budget_preserves_the_literal(seconds):
    message = f" 本轮时间预算 {seconds} 秒，到期保存已核验部分，可继续查询具体问题。"
    translated = text(message, "en")
    assert re.findall(r"\d+", translated) == [str(seconds)]
    assert not re.search(r"[\u4e00-\u9fff]", translated)


def test_historical_numeric_translation_remains_stable():
    # Historical exports compare numeric text byte for byte. Even a legacy
    # fragment bug in a label must not invalidate an old saved package.
    with language_scope("en"):
        assert legacy_numeric_text("420 秒") == "42 0 sec"
        assert text("420 秒") == "420 seconds"


def test_new_annual_period_gap_is_fully_localized_without_altering_year():
    message = "AAPL FY2025 同比不可计算：来源未绑定可对齐的相邻年度起止日，不能混用期间。"
    translated = text(message, "en")
    assert "FY2025" in translated and "aligned consecutive annual" in translated
    assert not re.search(r"[\u4e00-\u9fff]", translated)


@pytest.mark.parametrize("message,expected", [
    ("模型未在研究预算内提交完整回答；已保留通过核验的内容，可缩小问题范围后重试。",
     "The model did not submit a complete answer within the research budget. Verified content is retained; narrow the question and retry."),
    ("本轮未在预算内形成通过核验的完整结论；已保留可用证据。下一步：",
     "No complete, verified conclusion was produced within the budget. Available evidence is retained. Next step:"),
    ("12 份近期公告未完成可交付的中文摘要，已保留通过来源匹配的原文摘录；可单独查询“近期公司公告中文摘要”继续。",
     '12 recent announcements lack completed summaries. Source-matched excerpts are retained; ask "Summarize recent company updates" to continue.'),
])
def test_incomplete_research_gap_translates_without_rewriting_evidence(message, expected):
    from copy import deepcopy

    evidence = [{"id": "original", "text": "原始披露不得改写。"}]
    answer = ResearchAnswer(id="incomplete", question="question", as_of="2026-10-08",
                            mode="agent", status="partial", answer=[], gaps=[message],
                            evidence=deepcopy(evidence), trace=[], usage={}, elapsed_ms=0)
    prepared = prepare_answer(answer, "en")
    assert prepared.gaps == [expected]
    assert prepared.evidence == evidence
    assert prepared.status == "partial"
    assert text(message, "zh") == message
