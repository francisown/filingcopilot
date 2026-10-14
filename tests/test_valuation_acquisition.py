"""A direct first-time annual valuation request must acquire its required filing."""

import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from test_ads_valuation import ads_html
from test_foreign_financials import CIK, NativeFetcher
from test_market import NOW, URL, provider

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package
from filingcopilot.execution import ResearchCancelled, ResearchControl, research_scope
from filingcopilot.ifrs_sources import extract_ifrs_facts
from filingcopilot.ingestion import acquire_company, extract_passages
from filingcopilot.market import parse_chart
from filingcopilot.models import Filing, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.fixture
def cold_service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.register_company("TSM", {"cik": CIK, "name": "TSMC"})
    service.refresh_domain()
    service.discovery.fetcher = NativeFetcher(tmp_path / "sec-cache", ads_html())
    data = provider()
    data["chart"]["result"][0]["meta"].update(symbol="TSM", regularMarketPrice=457.99)
    snapshot = parse_chart(json.dumps(data).encode(), "TSM", "5Y", date(2026, 10, 8), NOW, URL)
    service.market.history = lambda *args, **kwargs: snapshot
    return service


def test_first_api_annual_valuation_acquires_original_and_returns_bound_ads_eps(cold_service):
    assert not cold_service.corpus.catalog(date(2026, 10, 8), "TSM")
    response = TestClient(create_app(cold_service)).get(
        "/api/market/valuation", params={"ticker": "TSM", "as_of": "2026-10-08"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["found"]
    assert result["evidence"]["operation"] == "annual_pe" and "ADS" in result["evidence"]["formula"]
    assert cold_service.corpus.catalog(date(2026, 10, 8), "TSM")


def test_first_research_valuation_acquires_without_manual_prepare_flag(cold_service):
    answer = cold_service.research(
        ResearchRequest(
            language="zh",
            question="年度市盈率是多少？", ticker="TSM", as_of=date(2026, 10, 8), mode="agent"
        )
    )
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert "43.91 倍" in answer.answer[0]["text"]
    assert any(t["stage"] == "annual_refresh" and t["result"]["checked"] for t in answer.trace)
    assert (
        audit_package(
            cold_service.export_package(answer.id),
            corpus=cold_service.corpus,
            source_cache=cold_service.settings.home / "sec-cache",
        )["status"]
        == "consistent"
    )


def test_cached_eps_does_not_start_an_optional_network_refresh(cold_service):
    cold_service.tools(date(2026, 10, 8)).get_stock_valuation("TSM")
    before = len(cold_service.discovery.fetcher.calls)
    assert cold_service.tools(date(2026, 10, 8)).get_stock_valuation("TSM")["found"]
    assert len(cold_service.discovery.fetcher.calls) == before


def test_first_company_is_registered_without_waiting_for_vector_index(
    cold_service, tmp_path, monkeypatch
):
    service = FilingCopilot(Settings(home=tmp_path / "unknown", embedding_mode="local"))
    service.discovery.fetcher = NativeFetcher(service.settings.home / "sec-cache", ads_html())
    service.market.history = cold_service.market.history
    monkeypatch.setattr(
        service.retriever, "_vectors", lambda *a: pytest.fail("Numeric EPS does not need vectors")
    )
    tools = service.tools(date(2026, 10, 8))
    assert "TSM" not in tools.domain.companies
    result = tools.get_stock_valuation("TSM")
    assert result["found"] and tools.domain.companies["TSM"].cik == CIK
    assert service.corpus.passages(date(2026, 10, 8), "TSM"), (
        "Do not leave a facts-only filing that blocks later RAG preparation"
    )


def test_one_report_eps_check_does_not_cache_a_two_report_refresh(cold_service):
    cold_service.tools(date(2026, 10, 8)).get_stock_valuation("TSM")
    before = len(cold_service.discovery.fetcher.calls)
    result = cold_service.discovery.refresh_annual("TSM", date(2026, 10, 8))
    assert result["checked"] and len(cold_service.discovery.fetcher.calls) > before
    assert ("TSM", "2026-10-08", "20-F:annual_eps") in cold_service.discovery.quarter_checks
    assert ("TSM", "2026-10-08", "20-F") in cold_service.discovery.quarter_checks


def test_single_tree_acquisition_preserves_standalone_fact_and_passage_ids(
    cold_service, monkeypatch
):
    from bs4 import BeautifulSoup

    parses = []

    def parse(*args, **kwargs):
        parses.append(1)
        return BeautifulSoup(*args, **kwargs)

    monkeypatch.setattr("filingcopilot.ingestion.BeautifulSoup", parse)
    entry = acquire_company(
        cold_service.discovery.fetcher,
        "TSM",
        years=1,
        domain=cold_service.domain,
        as_of=date(2026, 10, 8),
        forms=("20-F", "20-F/A"),
    )[0]
    assert len(parses) == 1
    filing = Filing.model_validate(entry["filing"])
    assert entry["facts"] == [
        f.model_dump(mode="json")
        for f in extract_ifrs_facts(ads_html(), filing, cold_service.domain)
    ]
    assert entry["passages"] == [
        p.model_dump(mode="json") for p in extract_passages(ads_html(), filing)
    ]


def test_acquisition_never_uses_a_filing_published_after_as_of(cold_service):
    tools = cold_service.tools(date(2026, 4, 15))
    result = tools.get_stock_valuation("TSM", 2025)
    assert not result["found"] and result["source_preparation"]["checked"]
    assert not tools.ledger and not cold_service.corpus.catalog(date(2026, 4, 15), "TSM")
    assert all("annual.htm" not in url for url in cold_service.discovery.fetcher.calls)


def test_missing_share_class_is_checked_once_per_investigation(cold_service):
    cold_service.discovery.fetcher.html = ads_html(member="tsm:OtherMember")
    tools = cold_service.tools(date(2026, 10, 8))
    result = tools.get_stock_valuation("TSM")
    count = len(cold_service.discovery.fetcher.calls)
    assert not result["found"] and "单独披露" in result["reason"]
    assert not tools.get_stock_valuation("TSM")["found"]
    assert len(cold_service.discovery.fetcher.calls) == count
    assert not any(e["kind"] == "market" for e in tools.ledger.values())


def test_network_failure_has_a_concrete_recovery_and_no_invented_eps(cold_service):
    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("controlled connection")

    cold_service.discovery.fetcher.fetch = fail
    result = cold_service.tools(date(2026, 10, 8)).get_stock_valuation("TSM")
    assert not result["found"] and not result["source_preparation"]["checked"]
    assert "ReadTimeout" in result["reason"] and "网络" in result["reason"]
    assert not cold_service.corpus.catalog(date(2026, 10, 8), "TSM")


def test_global_cancel_propagates_from_auto_acquisition(cold_service):
    control = ResearchControl()

    def cancel(*args, **kwargs):
        control.cancel()
        from filingcopilot.execution import checkpoint

        checkpoint()

    cold_service.discovery.fetcher.fetch = cancel
    with research_scope(control), pytest.raises(ResearchCancelled):
        cold_service.tools(date(2026, 10, 8)).get_stock_valuation("TSM")


def test_first_source_request_uses_short_budget_without_expiring_research(
    cold_service, monkeypatch
):
    from filingcopilot.execution import bounded_timeout, checkpoint

    clock = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: clock[0])
    control = ResearchControl()
    control.start_budget(240)
    timeouts = []

    def slow(*args, **kwargs):
        timeouts.append(bounded_timeout(30))
        clock[0] += timeouts[-1]
        checkpoint()

    cold_service.discovery.fetcher.fetch = slow
    with research_scope(control):
        result = cold_service.tools(date(2026, 10, 8)).get_stock_valuation("TSM")
        assert not result["found"] and "时间用完" in result["reason"]
        assert timeouts == [20] and control.remaining == 220 and not control.expired


def test_completed_original_survives_expired_freshness_check(cold_service, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: clock[0])
    original = cold_service.discovery._check_disclosures

    def expires_after_ingest(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 21
        return result

    monkeypatch.setattr(cold_service.discovery, "_check_disclosures", expires_after_ingest)
    answer = cold_service.research(
        ResearchRequest(language="zh", question="年度市盈率是多少？", ticker="TSM", as_of=date(2026, 10, 8))
    )
    assert answer.status == "partial" and "43.91 倍" in answer.answer[0]["text"]
    assert len(answer.gaps) == 1 and "最新申报检查未完成" in answer.gaps[0]
    assert not getattr(cold_service.discovery, "quarter_checks", {}), (
        "An expired check must not acquire a freshness TTL"
    )


def test_unsupported_listing_does_not_attempt_sec(cold_service):
    result = cold_service.tools(date(2026, 10, 8)).get_stock_valuation("2330.TW")
    assert not result["found"] and "尚未接入" in result["reason"]
    assert not cold_service.discovery.fetcher.calls
