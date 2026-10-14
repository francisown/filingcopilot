import re
from datetime import date

import pytest

from filingcopilot.api import create_app
from filingcopilot.localization import english_catalog
from filingcopilot.market import question_period
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


@pytest.mark.parametrize("question", [
    english_catalog()["股票怎么样？结合经营表现和价格分析。"],
    "Analyze this stock's financial performance and valuation.",
    "Analyse this stock's financial performance and valuation.",
    "Evaluate this stock's financial performance and valuation.",
])
def test_english_assessment_enters_stock_research_plan(tmp_path, question):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    plan = service.research_plan(ResearchRequest(
        question=question, ticker="AAPL", as_of=date(2026, 10, 8),
    ))
    assert plan["kind"] == "stock"


def test_ordinary_annual_fact_question_retains_numeric_plan(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    request = ResearchRequest(
        question="What were Apple's FY2025 revenue and net income?", ticker="AAPL",
        as_of=date(2026, 10, 8), mode="reference",
    )
    plan = service.research_plan(request)
    assert plan["kind"] == "filing" and plan["ready"] and not plan["uses_model"]
    answer = service.research(request)
    assert answer.usage["model_calls"] == 0
    assert len(answer.answer) == 2 and all(c["kind"] == "fact" for c in answer.answer)


@pytest.mark.parametrize("ticker", ["AAPL", "PDD", "BABA", "TSM"])
def test_every_advertised_preset_keeps_its_plan_in_english(tmp_path, ticker):
    from filingcopilot.localization import text

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    for question in service.capabilities(ticker)["questions"]:
        english = text(question, "en")
        chinese_plan = service.research_plan(ResearchRequest(
            question=question, ticker=ticker, as_of=date.today(), language="zh",
        ))
        english_plan = service.research_plan(ResearchRequest(
            question=english, ticker=ticker, as_of=date.today(),
        ))
        assert english_plan["kind"] == chinese_plan["kind"], english
        assert english_plan["ready"] == chinese_plan["ready"], english
        assert english_plan["uses_model"] == chinese_plan["uses_model"], english


@pytest.mark.parametrize("question,expected", [
    ("one-year stock price chart", "1Y"),
    ("stock returns over 1 year", "1Y"),
    ("five-year prices", "5Y"),
    ("stock returns over ten years", "10Y"),
    ("price chart over six months", "6M"),
    ("price chart over one month", "1M"),
    ("full price history", "MAX"),
    ("price history since IPO", "MAX"),
])
def test_native_english_price_windows(question, expected):
    assert question_period(question) == expected


@pytest.mark.parametrize("question", [
    "What happened over 35 years?",
    "How have revenue and cash flow changed over 35 years?",
    "Show the stock price over 35 years.",
])
def test_unsupported_compound_years_do_not_fall_into_a_short_window(question):
    from filingcopilot.market import unsupported_question_period
    from filingcopilot.trends import is_financial_trend_question

    assert unsupported_question_period(question)
    assert not is_financial_trend_question(question)


def test_unsupported_price_window_is_rejected_by_the_plan(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    plan = service.research_plan(ResearchRequest(
        question="Show the stock price over 35 years.", ticker="AAPL",
        as_of=date.today(),
    ))
    assert plan["ready"] is False
    assert "支持" in plan["message"]


def test_financial_year_and_ttm_months_are_not_chart_windows(tmp_path):
    from filingcopilot.market import unsupported_question_period

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    annual = service.research_plan(ResearchRequest(
        question="FY2025 年报披露了哪些主要风险？", ticker="TSM",
        as_of=date(2026, 10, 9), mode="reference", auto_acquire=True,
    ))
    ttm = service.research_plan(ResearchRequest(
        question="连续12个月经营现金流是多少？", ticker="AAPL", language="zh",
    ))
    assert not unsupported_question_period("FY2025 年报披露了哪些主要风险？")
    assert not unsupported_question_period("连续12个月经营现金流是多少？")
    assert annual["ready"] and ttm["ready"]


@pytest.mark.parametrize("question", [
    "Show the stock price over 35 years.",
    "What were the latest annual revenue and net income?",
    "What are the main risks disclosed in the latest annual report?",
])
def test_english_plan_endpoint_fields_do_not_leak_chinese(tmp_path, question):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    from fastapi.testclient import TestClient

    plan = TestClient(create_app(service)).post(
        "/api/research/plan",
        json={"question": question, "ticker": "AAPL", "as_of": "2026-10-08", "mode": "reference"},
    ).json()
    values = [plan.get("message", ""), plan.get("scope", ""), *plan.get("suggestions", [])]
    values += [plan.get("budget", {}).get("cost_basis", ""), plan.get("budget", {}).get("latency_basis", "")]
    assert not any(re.search(r"[\u4e00-\u9fff]", value) for value in values)


def test_english_research_errors_do_not_leak_mixed_language_details(tmp_path):
    from fastapi.testclient import TestClient

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    client = TestClient(create_app(service))
    future = date(2099, 1, 1).isoformat()
    english = client.post("/api/research", json={
        "question": "Analyze this stock's financial performance and valuation.",
        "ticker": "AAPL", "as_of": future,
    })
    assert english.status_code == 422
    assert not re.search(r"[\u4e00-\u9fff]", english.json()["detail"])
    assert "awareness date" in english.json()["detail"].lower()

    chinese = client.post("/api/research", json={
        "question": "分析这只股票的经营表现和估值。", "ticker": "AAPL",
        "as_of": future, "language": "zh",
    })
    assert chinese.status_code == 422
    assert re.search(r"[\u4e00-\u9fff]", chinese.json()["detail"])


def test_capex_is_not_a_cash_balance_request(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    frozen_configuration = service.domain.model_dump(mode="json")
    question = "What are TTM free cash flow and cash capital expenditures?"
    assert service.domain.allowed_metrics(question) == {"cash_capex"}
    assert service.research_plan(ResearchRequest(question=question, ticker="AAPL"))["ready"]
    # A requested cash balance is still a point-in-time metric, never a TTM flow.
    balance = "What are TTM cash balance and cash capital expenditures?"
    assert "cash" in service.domain.allowed_metrics(balance)
    assert not service.research_plan(ResearchRequest(question=balance, ticker="AAPL"))["ready"]
    assert service.domain.model_dump(mode="json") == frozen_configuration


def test_three_year_trend_and_update_summary_have_specific_routes(tmp_path):
    from filingcopilot.trends import trend_years
    from filingcopilot.updates import wants_update_summary

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    trend = "How have revenue and cash flow changed over three years?"
    assert service.research_plan(ResearchRequest(question=trend, ticker="AAPL"))["kind"] == "trend"
    assert trend_years(trend) == 3
    summary = english_catalog()["近期公司公告中文摘要"]
    assert wants_update_summary(summary)
    assert service.research_plan(ResearchRequest(question=summary, ticker="AAPL"))["kind"] == "updates"
