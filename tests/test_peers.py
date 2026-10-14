import copy
from datetime import date

import pytest
from test_comparison_valuation import provider_service as provider_service

from filingcopilot.audit import audit_package, digest
from filingcopilot.models import ResearchRequest
from filingcopilot.peers import candidate_peers, is_peer_question

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_peer_registry_is_explicit_and_only_returns_available_symbols():
    result = candidate_peers("AMZN", {"AMZN", "MSFT", "META", "AAPL"})
    assert result["found"]
    assert result["ticker"] == "AMZN"
    assert result["peers"] == ["AAPL", "MSFT", "META"]
    assert "正式行业分类" in result["basis"] or "正式行业分类" in result["note"]


@pytest.mark.parametrize(
    "question,expected",
    [
        ("亚马逊同行市盈率和近五年走势", True),
        ("比较同行的 TTM P/E", True),
        ("年报如何描述竞争优势？", False),
        ("AMZN peer valuation", True),
    ],
)
def test_peer_question_requires_an_explicit_valuation_comparison(question, expected):
    assert is_peer_question(question) is expected


def test_unknown_or_singleton_peer_group_is_actionable():
    assert not candidate_peers("JPM", {"JPM"})["found"]
    with pytest.raises(ValueError):
        candidate_peers("not a symbol", {"AAPL"})


def test_peer_question_plans_deterministic_comparison(provider_service):
    service, controls = provider_service
    request = ResearchRequest(
        question="微软同行市盈率和近一年走势",
        ticker="MSFT",
        as_of=date.today(),
    )
    plan = service.research_plan(request)
    assert plan["ready"] and plan["kind"] == "company_comparison"
    assert plan["comparison_tickers"] == ["MSFT", "AAPL", "NVDA"]
    assert plan["peer_selection"]["group_id"] == "large_technology_platforms"
    assert controls["calls"] == []
    answer = service.research(request)
    assert answer.mode == "comparison" and answer.usage["model_calls"] == 0
    package = service.export_package(answer.id)
    assert audit_package(package)["status"] == "consistent"
    bad = copy.deepcopy(package)
    bad["research"]["trace"][0]["result"]["peers"].append("AMZN")
    bad["manifest"]["payload_sha256"] = digest({k: v for k, v in bad.items() if k != "manifest"})
    assert audit_package(bad)["status"] == "invalid"


def test_question_named_company_overrides_unrelated_selection(provider_service):
    service, _ = provider_service
    plan = service.research_plan(ResearchRequest(question="微软同行市盈率", ticker="AAPL"))
    assert plan["comparison_tickers"][0] == "MSFT"
    assert not service.research_plan(ResearchRequest(question="微软和苹果的同行市盈率"))["ready"]
