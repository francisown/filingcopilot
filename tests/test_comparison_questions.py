from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_comparison_valuation import DAY
from test_comparison_valuation import provider_service as provider_service

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package
from filingcopilot.comparison import VALUATION_SCHEMA, question_ttm_companies
from filingcopilot.domain import Company
from filingcopilot.fundamentals import is_stock_question
from filingcopilot.models import ResearchRequest
from filingcopilot.valuation import is_provider_valuation_question

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.mark.parametrize(
    "question,labels",
    [
        ("比较微软和苹果的 TTM 每股收益和市盈率，近五年走势", ["微软", "苹果"]),
        ("MSFT、AAPL的TTM市盈率是多少？", ["MSFT", "AAPL"]),
        ("MSFT vs. AAPL TTM EPS与市盈率对比", ["MSFT", "AAPL"]),
        ("compare msft and aapl TTM EPS and P/E over 1 year price chart", ["msft", "aapl"]),
        ("苹果与微软的供应商 TTM EPS 对比，全部历史走势", ["苹果", "微软"]),
        ("比较微软和不存在公司 TTM EPS", ["微软", "不存在公司"]),
        ("微软和苹果滚动十二个月每股收益比较", ["微软", "苹果"]),
    ],
)
def test_explicit_numeric_company_list_preserves_every_requested_name(question, labels):
    assert question_ttm_companies(question) == labels


@pytest.mark.parametrize(
    "question",
    [
        "供应商 TTM 每股收益和市盈率是多少？",
        "微软 TTM EPS 是多少？",
        "比较微软和苹果年度 EPS 市盈率",
        "比较微软和苹果 TTM 市盈率，为什么微软贵？",
        "比较微软和苹果 TTM EPS 及营收",
        "比较微软和苹果 TTM 市盈率和 SPY 基准走势",
        "比较微软和苹果 TTM EPS 与 NVDA",
        "比较微软和苹果 TTM EPS 与另一家公司",
        "比较微软和苹果 TTM forward P/E",
        "微软和苹果股票怎么样，TTM 市盈率合理吗？",
    ],
)
def test_single_mixed_or_qualitative_question_keeps_existing_path(question, provider_service):
    assert question_ttm_companies(question) is None
    service, controls = provider_service
    if (
        is_provider_valuation_question(question)
        and not is_stock_question(question)
        and len(service.domain.company_scope(question)) > 1
    ):
        plan = service.research_plan(ResearchRequest(question=question, as_of=DAY))
        assert not plan["ready"] and plan["kind"] == "company_comparison"
        assert plan["suggestions"] and not plan["comparison_tickers"]
        assert controls["calls"] == []


def test_free_question_resolves_aliases_before_any_io_and_uses_comparison(provider_service):
    service, controls = provider_service
    prepared = []
    service.discovery.prepare_valuation = lambda *a, **k: pytest.fail(
        "TTM numeric comparison must not wait for a supplementary annual refresh"
    )
    service.discovery.prepare = lambda *a, **k: pytest.fail("no single-company preparation")
    service.gateway = None
    query = ResearchRequest(
        question="比较微软和苹果的 TTM 每股收益和市盈率，近一年走势",
        ticker="NVDA",  # Explicit question scope overrides the unrelated desk selection.
        as_of=DAY,
        auto_acquire=True,
    )
    plan = service.research_plan(query)
    assert plan["ready"] and plan["kind"] == "company_comparison"
    assert plan["comparison_tickers"] == ["MSFT", "AAPL"] and not plan["uses_model"]
    assert controls["calls"] == [] and prepared == []
    answer = service.research(query)
    assert answer.question == query.question and answer.mode == "comparison"
    result = answer.trace[0]["result"]
    assert result["schema_version"] == VALUATION_SCHEMA
    assert result["tickers"] == ["MSFT", "AAPL"] and result["period"] == "1Y"
    assert prepared == [] and sorted(controls["calls"]) == ["AAPL", "MSFT"]
    assert answer.usage["model_calls"] == 0
    assert any(c["kind"] == "calculation" for c in answer.answer)
    assert not any("Requested metric missing" in gap or "claim " in gap for gap in answer.gaps)
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize(
    "question,day,reason",
    [
        ("比较微软和不存在公司 TTM EPS", DAY, "尚未明确匹配"),
        ("比较微软和 MSFT TTM EPS", DAY, "不同公司"),
        ("微软、苹果、英伟达、AMZN、META TTM EPS", DAY, "2 至 4"),
        ("微软和苹果 TTM EPS", DAY - timedelta(days=1), "仅支持今天"),
        ("微软和苹果 TTM EPS，近三年走势", DAY, "一年、五年和全部"),
        ("微软和苹果 TTM EPS，近六个月走势", DAY, "一年、五年和全部"),
    ],
)
def test_invalid_scope_has_actionable_preflight_and_never_fetches(
    provider_service, question, day, reason
):
    service, controls = provider_service
    service.discovery.prepare_valuation = lambda *a, **k: pytest.fail("blocked request did IO")
    query = ResearchRequest(question=question, as_of=day)
    plan = service.research_plan(query)
    assert not plan["ready"] and plan["kind"] == "company_comparison"
    assert reason in plan["message"] and plan["suggestions"]
    assert not plan["comparison_tickers"]
    with pytest.raises(ValueError, match=reason):
        service.research(query)
    assert controls["calls"] == []


def test_public_plan_and_research_accept_explicit_list_without_a_selected_company(provider_service):
    service, _ = provider_service
    service.discovery.prepare_valuation = lambda *a, **k: {"checked": True}
    payload = {"question": "比较苹果和微软 TTM EPS 和市盈率", "as_of": DAY.isoformat()}
    with TestClient(create_app(service)) as client:
        plan = client.post("/api/research/plan", json=payload)
        assert plan.status_code == 200 and plan.json()["ready"]
        response = client.post("/api/research", json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["mode"] == "comparison" and result["usage"]["model_calls"] == 0
        assert result["question"] == payload["question"]
        assert client.get(f"/api/runs/{result['id']}/audit").json()["status"] == "consistent"


def test_shared_listing_alias_is_verified_and_multi_listing_name_requires_selection(
    provider_service,
):
    service, controls = provider_service
    not_registered = service.research_plan(ResearchRequest(question="比较微软和亚马逊 TTM EPS"))
    assert not not_registered["ready"] and "首次登记" in not_registered["message"]
    service.domain = service.domain.model_copy(
        update={
            "companies": {
                **service.domain.companies,
                "AMZN": Company(cik="0001018724", name="AMAZON COM INC"),
            }
        }
    )
    service.discovery.prepare_valuation = lambda *a, **k: {"checked": True}
    query = ResearchRequest(question="比较微软和亚马逊 TTM EPS 和市盈率", as_of=DAY)
    plan = service.research_plan(query)
    assert plan["ready"] and plan["comparison_tickers"] == ["MSFT", "AMZN"]
    assert service.domain.company_scope(query.question) == {"MSFT", "AMZN"}
    answer = service.research(query)
    assert not any("does not match the research scope" in gap for gap in answer.gaps)
    assert sorted(controls["calls"]) == ["AMZN", "MSFT"]
    assert (
        audit_package(service.export_package(answer.id), trusted_domain=service.domain)["status"]
        == "consistent"
    )
    ambiguous = service.research_plan(ResearchRequest(question="比较微软和台积电 TTM EPS"))
    assert not ambiguous["ready"] and "尚未明确匹配" in ambiguous["message"]
    assert service.find_stock("亚马逊", translate=False)["candidates"][0]["ticker"] == "AMZN"
