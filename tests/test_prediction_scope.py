"""Prediction boundaries must run before quote, quarter and peer routing."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("ticker", ["AAPL", "TSM"])
@pytest.mark.parametrize("question,language,reason", [
    ("Can the annual filing alone predict Apple's next-quarter stock price? State the evidence gap.", "en", "future stock price"),
    ("仅凭年报能否判断下季度股价？请说明缺口。", "zh", "未来股价"),
    ("Is it proven that this company will outperform peers, or should the answer abstain?", "en", "outperformance"),
    ("是否一定会跑赢同行？如果证据不足应如何回答？", "zh", "跑赢"),
    ("Predict next month's stock price.", "en", "future stock price"),
    ("What will the stock price be next quarter?", "en", "future stock price"),
    ("Will Apple's stock price rise next quarter?", "en", "future stock price"),
    ("这只股票下个月会涨多少？", "zh", "未来股价"),
])
def test_unsupported_prediction_explains_the_actual_boundary(tmp_path, monkeypatch, ticker, question, language, reason):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)

    def unexpected(*args, **kwargs):
        pytest.fail("Prediction boundary must precede data acquisition or quarterly/peer work")

    monkeypatch.setattr(service, "tools", unexpected)
    monkeypatch.setattr(service.discovery, "prepare", unexpected)
    request = ResearchRequest(question=question, ticker=ticker, language=language, as_of=date(2026, 10, 8))
    client = TestClient(create_app(service))
    payload = request.model_dump(mode="json")
    plan = client.post("/api/research/plan", json=payload).json()
    assert not plan["ready"]
    assert not plan["uses_model"]
    assert reason in plan["message"]
    assert "TTM EPS" not in plan["message"] and "10-Q" not in plan["message"]
    assert plan["suggestions"]
    response = client.post("/api/research", json=payload)
    assert response.status_code == 422
    assert reason in response.json()["detail"]


@pytest.mark.parametrize("question,kind", [
    ("What is Apple's stock price and five-year return?", "market"),
    ("过去五年股价涨跌和最大回撤是多少？", "market"),
    ("最新季度营收是多少？", "quarterly"),
    ("What risks could cause Apple to underperform peers?", "filing"),
    ("What competitive risks are disclosed in the annual filing?", "filing"),
    ("What factors could help Apple outperform peers?", "filing"),
    ("Compare Apple's peer P/E multiples.", "company_comparison"),
    ("苹果同行PE", "company_comparison"),
    ("苹果同行市盈率", "company_comparison"),
])
def test_supported_research_is_not_a_prediction(tmp_path, question, kind):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    plan = service.research_plan(ResearchRequest(question=question, ticker="AAPL"))
    assert plan["kind"] == kind
    assert plan["ready"]


def test_future_awareness_date_still_precedes_forecast_boundary(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)
    plan = service.research_plan(ResearchRequest(
        question="Predict Apple's stock price.", ticker="AAPL",
        as_of=date.today() + timedelta(days=1), language="zh",
    ))
    assert not plan["ready"]
    assert "知悉日期不能晚于" in plan["message"]


@pytest.mark.parametrize("question,language", [
    ("如果行情源不可用，苹果的估值问题应如何返回？", "zh"),
    ("How should the system respond when the market source for Apple is unavailable?", "en"),
    ("苹果的年报仍可用但实时价格失败时，哪些结论可以保留？", "zh"),
    ("If Apple's filing is available but its quote fails, which conclusions remain valid?", "en"),
])
def test_hypothetical_failure_policy_does_not_fetch_live_quotes(tmp_path, monkeypatch, question, language):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"), gateway=False)

    def unexpected(*args, **kwargs):
        pytest.fail("A policy explanation must not probe the live market or model")

    monkeypatch.setattr(service, "tools", unexpected)
    client = TestClient(create_app(service))
    payload = ResearchRequest(question=question, ticker="AAPL", language=language).model_dump(mode="json")
    plan = client.post("/api/research/plan", json=payload).json()
    assert not plan["ready"] and not plan["uses_model"]
    assert ("如果" if language == "zh" else "If") in plan["message"]
    assert ("未实际" if language == "zh" else "not an observed") in plan["message"]
    response = client.post("/api/research", json=payload)
    assert response.status_code == 422
    assert ("保留" if language == "zh" else "retain") in response.json()["detail"]
