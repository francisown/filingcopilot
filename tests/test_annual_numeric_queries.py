"""Exact annual questions should fetch the requested year, not loop through a model."""

import hashlib
import json
from datetime import date

import httpx
import pytest

from filingcopilot.audit import audit_package
from filingcopilot.ingestion import acquire_company
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

pytestmark = pytest.mark.slow


class NoModel:
    def complete(self, *args, **kwargs):
        pytest.fail("A typed annual lookup must not call a model")


class HistoricalFetcher:
    """Synthetic SEC responses; amounts below are fixture values, not NVIDIA facts."""

    def __init__(self, cache, *, income=True, old_filing_date="2022-03-17"):
        self.cache = cache
        self.calls = []
        self.income = income
        self.old_filing_date = old_filing_date

    def fetch(self, url, offline=False, max_age=None):
        self.calls.append(url)
        if "company_tickers" in url:
            value = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA"}}
        elif "/submissions/" in url and url.endswith("CIK0001045810.json"):
            value = {"cik": 1045810, "tickers": ["NVDA"], "filings": {
                "recent": {"form": ["10-K"], "filingDate": ["2026-02-25"],
                           "reportDate": ["2026-01-25"],
                           "accessionNumber": ["0001045810-26-000001"],
                           "primaryDocument": ["latest.htm"]},
                "files": [{"name": "CIK0001045810-submissions-001.json",
                           "filingFrom": "2020-01-01", "filingTo": "2024-12-31"}]}}
        elif url.endswith("CIK0001045810-submissions-001.json"):
            value = {"form": ["10-K"], "filingDate": [self.old_filing_date],
                     "reportDate": ["2022-01-30"],
                     "accessionNumber": ["0001045810-22-000001"],
                     "primaryDocument": ["historic.htm"]}
        elif "/companyfacts/" in url:
            row = {"accn": "0001045810-22-000001", "form": "10-K",
                   "start": "2021-02-01", "end": "2022-01-30", "val": 100}
            values = {"Revenues": {"units": {"USD": [row]}}}
            if self.income:
                values["NetIncomeLoss"] = {"units": {"USD": [{**row, "val": 40}]}}
            value = {"cik": 1045810, "facts": {"us-gaap": values}}
        elif url.endswith("historic.htm"):
            value = None
        else:
            pytest.fail("Unrequested latest filing or unexpected source")
        raw = (json.dumps(value).encode() if value is not None
               else b"<p>Synthetic historical annual filing, used to test source identity and fiscal year selection only.</p>")
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")).write_bytes(raw)
        return raw


def service_at(tmp_path, **fetcher_options):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="local"), gateway=NoModel())
    service.discovery.fetcher = HistoricalFetcher(tmp_path / "sec-cache", **fetcher_options)
    service.retriever._vectors = lambda *args: pytest.fail("Numbers must not build a vector index")
    return service


def historical_request(**kwargs):
    return ResearchRequest(language="zh", question="What were NVIDIA's FY2022 revenue and net income?",
                           ticker="NVDA", as_of=date(2026, 10, 8), **kwargs)


def test_frozen_numeric_failure_gets_requested_annual_sources_without_model_or_index(tmp_path):
    service = service_at(tmp_path)
    request = historical_request(auto_acquire=True)
    plan = service.research_plan(request)
    assert plan["ready"] and plan["annual_numeric_direct"] and not plan["uses_model"]
    answer = service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert len(answer.answer) == 2
    assert {e["fiscal_year"] for e in answer.evidence if e["kind"] == "fact"} == {2022}
    assert {e["value"] for e in answer.evidence if e["kind"] == "fact"} == {"100", "40"}
    assert any("submissions-001.json" in url for url in service.discovery.fetcher.calls)
    assert not any(url.endswith("latest.htm") for url in service.discovery.fetcher.calls)
    package = service.export_package(answer.id)
    report = audit_package(package, corpus=service.corpus, source_cache=tmp_path / "sec-cache")
    assert report["status"] == "consistent", report


def test_missing_metric_keeps_available_fact_and_specific_gap_without_model_repair(tmp_path):
    service = service_at(tmp_path, income=False)
    answer = service.research(historical_request())
    assert answer.status == "partial" and answer.usage["model_calls"] == 0
    assert len(answer.answer) == 1
    assert any("2022" in gap and "净利润" in gap for gap in answer.gaps)
    assert not any(e.get("fiscal_year") == 2026 for e in answer.evidence)


def test_source_public_date_is_not_bypassed_to_fill_requested_year(tmp_path):
    service = service_at(tmp_path)
    request = historical_request().model_copy(update={"as_of": date(2022, 3, 1)})
    answer = service.research(request)
    assert answer.status == "abstained" and not answer.answer
    assert answer.usage["model_calls"] == 0
    assert not any(url.endswith("historic.htm") for url in service.discovery.fetcher.calls)


def test_network_failure_is_actionable_and_does_not_leak_exception_or_call_model(tmp_path):
    service = service_at(tmp_path)

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("private transport token=fixture-secret")

    service.discovery.fetcher.fetch = unavailable
    answer = service.research(historical_request())
    assert answer.status == "abstained" and answer.usage["model_calls"] == 0
    assert any("2022" in gap for gap in answer.gaps)
    assert any("重试" in gap for gap in answer.gaps)
    assert "fixture-secret" not in json.dumps(answer.model_dump(mode="json"))


@pytest.mark.parametrize("question", [
    "苹果 FY2025 营收是多少？", "What was Apple's FY2025 revenue?",
    "苹果 FY2025 营收同比是多少？", "苹果 FY2025 毛利率是多少？",
])
def test_cached_typed_numbers_need_neither_sources_nor_model(tmp_path, question):
    service = service_at(tmp_path)
    service.discovery.fetcher.fetch = lambda *args, **kwargs: pytest.fail("Bound cached year is sufficient")
    answer = service.research(ResearchRequest(language="zh", question=question, ticker="AAPL", as_of="2026-10-08"))
    assert answer.status == "answered" and answer.usage["model_calls"] == 0


@pytest.mark.parametrize("question", [
    "Why did Apple's revenue increase?", "苹果营收增长是否可持续？",
    "Forecast Apple's FY2027 revenue", "苹果营收的来源和竞争优势是什么？",
])
def test_explanatory_or_forecast_questions_keep_the_analysis_route(tmp_path, question):
    service = service_at(tmp_path)
    plan = service.research_plan(ResearchRequest(language="zh", question=question, ticker="AAPL", as_of="2026-10-08"))
    assert not plan["annual_numeric_direct"]


def test_explicit_year_targets_are_bounded_and_not_supported_for_quarter_forms(tmp_path):
    service = service_at(tmp_path)
    with pytest.raises(ValueError):
        acquire_company(service.discovery.fetcher, "NVDA", domain=service.domain,
                        fiscal_years=(2022, 2023, 2024, 2025))
    with pytest.raises(ValueError):
        acquire_company(service.discovery.fetcher, "NVDA", domain=service.domain,
                        forms=("10-Q", "10-Q/A"), fiscal_years=(2022,))


def test_future_annual_year_has_gap_without_fetching_unrelated_reports(tmp_path):
    service = service_at(tmp_path)
    service.discovery.fetcher.fetch = lambda *args, **kwargs: pytest.fail("Future annual report cannot exist")
    answer = service.research(ResearchRequest(language="zh", question="苹果FY2030营收是多少？", ticker="AAPL", as_of="2026-10-08"))
    assert answer.status == "abstained" and answer.usage["model_calls"] == 0
    assert any("2030" in gap for gap in answer.gaps)


def test_offline_reference_missing_year_does_not_implicitly_acquire(tmp_path):
    service = service_at(tmp_path)
    service.discovery.fetcher.fetch = lambda *args, **kwargs: pytest.fail("Offline reference must use existing evidence")
    answer = service.research(historical_request(mode="reference", auto_acquire=False))
    assert answer.status == "abstained" and answer.usage["model_calls"] == 0
    assert any("2022" in gap for gap in answer.gaps)


def test_reference_explicit_acquisition_can_fetch_missing_annual_year(tmp_path):
    service = service_at(tmp_path)
    answer = service.research(historical_request(mode="reference", auto_acquire=True))
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert len(answer.answer) == 2
