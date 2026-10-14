import json
import threading
import time
from dataclasses import replace
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package
from filingcopilot.discovery import DIRECTORY_URL
from filingcopilot.domain import Company
from filingcopilot.ingestion import SecFetcher, acquire_company
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


class FixtureFetcher:
    def __init__(self):
        self.calls = []
        self.submissions_cik = 1318605
        self.facts_cik = 1318605
        self.ticker = "TSLA"
        self.form = "10-K"
        self.fail = False

    def fetch(self, url, offline=False, max_age=None):
        self.calls.append(url)
        if self.fail:
            raise httpx.ConnectError("fixture connection failure")
        if url == DIRECTORY_URL:
            payload = {
                "0": {"ticker": "TSLA", "title": "Tesla, Inc.", "cik_str": 1318605},
                "1": {"ticker": "TESTA", "title": "United Example One Inc.", "cik_str": 111},
                "2": {"ticker": "TESTB", "title": "United Example Two Inc.", "cik_str": 222},
            }
        elif "/submissions/" in url:
            payload = {
                "cik": self.submissions_cik,
                "tickers": [self.ticker],
                "filings": {
                    "recent": {
                        "form": [self.form] * 3,
                        "accessionNumber": [
                            "0001318605-27-000001",
                            "0001318605-26-000001",
                            "0001318605-25-000001",
                        ],
                        "filingDate": ["2027-01-29", "2026-01-29", "2025-01-29"],
                        "reportDate": ["2026-12-31", "2025-12-31", "2024-12-31"],
                        "primaryDocument": [
                            "tsla-20261231.htm",
                            "tsla-20251231.htm",
                            "tsla-20241231.htm",
                        ],
                    },
                    "files": [],
                },
            }
        elif "/companyfacts/" in url:
            payload = {
                "cik": self.facts_cik,
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {
                                        "accn": "0001318605-26-000001",
                                        "form": "10-K",
                                        "start": "2025-01-01",
                                        "end": "2025-12-31",
                                        "val": 1000,
                                    },
                                    {
                                        "accn": "0001318605-25-000001",
                                        "form": "10-K",
                                        "start": "2024-01-01",
                                        "end": "2024-12-31",
                                        "val": 800,
                                    },
                                ]
                            }
                        }
                    }
                },
            }
        else:
            text = (
                "The company manufactures electric vehicles and energy products. Demand, competition and supply chain disruptions may affect operating performance. "
                * 2
            )
            return f'<html><div id="risk"><p>{text}</p></div></html>'.encode()
        return json.dumps(payload).encode()


@pytest.fixture
def discovery_service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.discovery.fetcher = FixtureFetcher()
    yield service
    service.discovery.executor.shutdown(wait=True)


def test_existing_company_lookup_and_preparation_need_no_network(discovery_service):
    service = discovery_service
    assert service.discovery.search("苹果", False)["candidates"][0]["ticker"] == "AAPL"
    assert service.discovery.prepare("AAPL", date(2026, 10, 8))["source"] == "local"
    assert service.discovery.fetcher.calls == []


@pytest.mark.parametrize("query", ["TSLA", "Tesla", "Tesla, Inc.", "1318605", "0001318605"])
def test_official_name_ticker_and_cik_lookup(discovery_service, query):
    result = discovery_service.discovery.search(query, False)
    assert result["match_count"] == 1
    assert result["candidates"][0]["cik"] == "0001318605"
    assert result["translation"] is None


def test_ambiguous_name_does_not_pick_first_company(discovery_service):
    result = discovery_service.discovery.search("United", False)
    assert result["needs_selection"] and result["match_count"] == 2
    with pytest.raises(ValueError, match="多个"):
        discovery_service.research(
            ResearchRequest(question="FY2025 营收", company="United", mode="reference")
        )


def test_unknown_chinese_name_never_calls_model_in_reference_mode(discovery_service):
    with pytest.raises(ValueError, match="英文名"):
        discovery_service.discovery.search("特斯拉", False)
    assert not discovery_service.discovery.fetcher.calls


def test_translation_must_match_official_directory(discovery_service):
    class Gateway:
        def complete(self, messages, tools):
            assert tools is None
            return {
                "message": {"content": '{"name":"Tesla"}'},
                "model": "fixture",
                "usage": {"total_tokens": 10},
            }

    discovery_service.gateway = Gateway()
    result = discovery_service.discovery.search("特斯拉")
    assert result["candidates"][0]["ticker"] == "TSLA"
    assert result["translation"]["usage"]["total_tokens"] == 10
    assert "TSLA" not in discovery_service.domain.companies


def test_unknown_directory_name_reports_coverage(discovery_service):
    result = discovery_service.discovery.search("Nonexistent Global Company", False)
    assert result["candidates"] == [] and "其他市场" in result["message"]


def test_prepare_acquires_indexes_and_persists_identity(discovery_service):
    service = discovery_service
    result = service.discovery.prepare("TSLA", date(2026, 10, 8))
    assert result["status"] == "ready" and len(result["filings"]) == 2
    assert len(result["acquired"]) == 2
    assert service.domain.companies["TSLA"].cik == "0001318605"
    restarted = FilingCopilot(service.settings)
    try:
        assert restarted.domain.companies["TSLA"].name == "Tesla, Inc."
        assert restarted.tools(date(2026, 10, 8)).get_financial_fact("TSLA", "revenue", 2025)[
            "found"
        ]
    finally:
        restarted.discovery.executor.shutdown(wait=True)


def test_historical_preparation_does_not_download_future_reports(discovery_service):
    result = discovery_service.discovery.prepare("TSLA", date(2025, 2, 1))
    assert len(result["filings"]) == 1
    assert result["filings"][0]["filed"] == "2025-01-29"
    calls = discovery_service.discovery.fetcher.calls
    assert not any("tsla-20251231" in url or "tsla-20261231" in url for url in calls)
    assert not discovery_service.tools(date(2025, 2, 1)).get_financial_fact(
        "TSLA", "revenue", 2025
    )["found"]


def test_company_input_research_automatically_prepares_and_freezes(discovery_service):
    answer = discovery_service.research(
        ResearchRequest(
            question="FY2025 营业收入是多少？",
            company="Tesla",
            as_of="2026-10-08",
            mode="reference",
        )
    )
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    package = discovery_service.export_package(answer.id)
    assert package["focus_ticker"] == "TSLA" and "TSLA" in package["domain"]["companies"]
    assert discovery_service.audit_run(answer.id)["status"] == "consistent"


def test_adding_company_does_not_invalidate_older_package(discovery_service):
    service = discovery_service
    answer = service.research(
        ResearchRequest(question="苹果 FY2025 营收", mode="reference", as_of="2026-10-08")
    )
    package = service.export_package(answer.id)
    service.discovery.prepare("TSLA", date(2026, 10, 8))
    assert "TSLA" not in package["domain"]["companies"]
    assert service.audit_run(answer.id)["status"] == "consistent"
    tampered = service.export_package(answer.id)
    tampered["domain"]["companies"]["AAPL"]["cik"] = "0001318605"
    assert audit_package(tampered, trusted_domain=service.domain)["status"] == "invalid"


@pytest.mark.parametrize("field", ["submissions_cik", "facts_cik"])
def test_source_identity_mismatch_rejected_before_registration(discovery_service, field):
    setattr(discovery_service.discovery.fetcher, field, 999)
    with pytest.raises(ValueError, match="identity"):
        discovery_service.discovery.prepare("TSLA", date(2026, 10, 8))
    assert not discovery_service.corpus.catalog(date(2026, 10, 8), "TSLA")
    assert "TSLA" not in discovery_service.corpus.registered_companies()


def test_unsupported_foreign_annual_report_is_explicit(discovery_service):
    discovery_service.discovery.fetcher.form = "20-F"
    with pytest.raises(ValueError, match="20-F"):
        discovery_service.discovery.prepare("TSLA", date(2026, 10, 8))
    assert "TSLA" not in discovery_service.domain.companies


def test_registered_ticker_cannot_change_identity(discovery_service):
    corpus = discovery_service.corpus
    corpus.register_company("TSLA", Company(cik="0001318605", name="Tesla"))
    with pytest.raises(ValueError, match="identity"):
        corpus.register_company("TSLA", Company(cik="0000320193", name="Apple"))


def test_sec_ticker_mismatch_rejected_before_import(discovery_service):
    discovery_service.discovery.fetcher.ticker = "AAPL"
    with pytest.raises(ValueError, match="ticker"):
        discovery_service.discovery.prepare("TSLA", date(2026, 10, 8))
    assert not discovery_service.corpus.catalog(date(2026, 10, 8), "TSLA")


def test_impossible_publication_period_does_not_download_html(discovery_service):
    fetcher = discovery_service.discovery.fetcher
    original = fetcher.fetch

    def impossible(url, *args, **kwargs):
        data = original(url, *args, **kwargs)
        if "/submissions/" in url:
            payload = json.loads(data)
            payload["filings"]["recent"]["reportDate"][1] = "2027-12-31"
            return json.dumps(payload).encode()
        return data

    fetcher.fetch = impossible
    with pytest.raises(ValueError, match="publication"):
        discovery_service.discovery.prepare("TSLA", date(2026, 10, 8))
    assert not any("/Archives/" in url for url in fetcher.calls)


def test_job_singleflight_and_observable_result(discovery_service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    prepare = discovery_service.discovery.prepare

    def gated(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(discovery_service.discovery, "prepare", gated)
    client = TestClient(create_app(discovery_service))
    body = {"ticker": "TSLA", "as_of": "2026-10-08"}
    first = client.post("/api/companies/prepare", json=body).json()
    assert entered.wait(2)
    second = client.post("/api/companies/prepare", json=body).json()
    assert first["id"] == second["id"]
    release.set()
    for _ in range(100):
        job = client.get("/api/companies/jobs/" + first["id"]).json()
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "ready" and job["result"]["source"] == "sec"
    assert client.get("/api/health").json()["company_discovery"]


def test_failed_download_job_is_not_ready(discovery_service):
    discovery_service.discovery.fetcher.fail = True
    job = discovery_service.discovery.start("TSLA", date(2026, 10, 8))
    for _ in range(100):
        job = discovery_service.discovery.job(job["id"])
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "failed" and "连接 SEC" in job["error"]
    assert job["result"] is None


def test_first_annual_preview_survives_second_download_failure(discovery_service):
    fetcher = discovery_service.discovery.fetcher
    original = fetcher.fetch

    def fetch(url, *args, **kwargs):
        if 'tsla-20241231.htm' in url:
            raise httpx.ConnectError('second original unavailable')
        return original(url, *args, **kwargs)

    fetcher.fetch = fetch
    job = discovery_service.discovery.start('TSLA', date(2026, 10, 8))
    for _ in range(100):
        job = discovery_service.discovery.job(job['id'])
        if job['status'] != 'running':
            break
        time.sleep(.01)
    assert job['status'] == 'failed'
    assert job.get('preview') is not None
    assert job['preview']['ticker'] == 'TSLA'
    assert job['progress']['completed_reports'] == 1
    assert job['progress']['total_reports'] == 2
    assert len(discovery_service.corpus.catalog(date(2026, 10, 8), 'TSLA')) == 1
    fetcher.fetch = original
    # A partial cache must not make an explicit retry skip the missing report.
    retried = discovery_service.discovery.prepare('TSLA', date(2026, 10, 8))
    assert len(retried['acquired']) == 2
    assert len(retried['filings']) == 2


def test_preparation_publishes_verified_numbers_before_cold_index(discovery_service):
    service = discovery_service
    service.settings = replace(service.settings, embedding_mode='local')
    entered, release = threading.Event(), threading.Event()

    def vectors(passages):
        entered.set()
        assert release.wait(5)
        return None

    service.retriever._vectors = vectors
    job = service.discovery.start('TSLA', date(2026, 10, 8))
    try:
        assert entered.wait(3)
        current = service.discovery.job(job['id'])
        assert current['status'] == 'running'
        assert current.get('preview') is not None
        assert current['preview']['claims'][0]['verification'] == 'deterministic'
        assert current['progress']['completed_reports'] == 2
        assert current['progress']['facts'] == 2
        assert current['first_numbers_ms'] <= current['elapsed_ms']
    finally:
        release.set()


@pytest.mark.parametrize(
    "url",
    [
        "https://data.sec.gov.evil.test/test",
        "https://data.sec.gov@evil.test/test",
        "http://data.sec.gov/test",
        "https://www.sec.gov/files/private.json",
        "https://data.sec.gov:444/test",
    ],
)
def test_fetcher_allowlist_cannot_be_escaped(tmp_path, url):
    with pytest.raises(ValueError, match="official SEC"):
        SecFetcher(tmp_path, "").fetch(url)


def test_fetcher_directory_cache_and_offline_do_not_require_contact(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "filingcopilot.ingestion.httpx.get",
        lambda url, **kwargs: httpx.Response(
            200, content=b'{"0":{}}', request=httpx.Request("GET", url)
        ),
    )
    fetcher = SecFetcher(tmp_path, "fixture@example.test")
    assert fetcher.fetch(DIRECTORY_URL) == b'{"0":{}}'
    assert SecFetcher(tmp_path, "").fetch(DIRECTORY_URL, offline=True, max_age=0) == b'{"0":{}}'


def test_archived_submissions_are_used_for_historical_date(discovery_service):
    base = discovery_service.discovery.fetcher
    original = base.fetch

    def archived(url, *args, **kwargs):
        data = original(url, *args, **kwargs)
        if url.endswith("CIK0001318605.json") and "/submissions/" in url:
            payload = json.loads(data)
            payload["filings"]["files"] = [
                {
                    "name": "CIK0001318605-submissions-001.json",
                    "filingFrom": "2023-01-01",
                    "filingTo": "2024-12-31",
                }
            ]
            return json.dumps(payload).encode()
        if url.endswith("-submissions-001.json"):
            return json.dumps(
                {
                    "form": ["10-K"],
                    "accessionNumber": ["0001318605-24-000001"],
                    "filingDate": ["2024-01-29"],
                    "reportDate": ["2023-12-31"],
                    "primaryDocument": ["tsla-20231231.htm"],
                }
            ).encode()
        return data

    base.fetch = archived
    discovery_service.discovery.prepare("TSLA", date(2026, 10, 8))
    entries = acquire_company(
        base, "TSLA", 2, domain=discovery_service.domain, as_of=date(2024, 2, 1)
    )
    assert len(entries) == 1 and entries[0]["filing"]["filed"] == "2024-01-29"


@pytest.mark.parametrize("status", [None, 502, 429])
def test_fetcher_retries_transient_failures_without_cache_corruption(tmp_path, monkeypatch, status):
    clock = [1000.0]
    attempts = []
    monkeypatch.setattr("filingcopilot.ingestion.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "filingcopilot.ingestion.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    monkeypatch.setattr(SecFetcher, "_last_request", 0)

    def request(url, **kwargs):
        attempts.append(clock[0])
        if len(attempts) == 1:
            if status is None:
                raise httpx.ConnectError("fixture disconnect")
            return httpx.Response(
                status, headers={"retry-after": "2"}, request=httpx.Request("GET", url)
            )
        return httpx.Response(200, content=b"{}", request=httpx.Request("GET", url))

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", request)
    assert SecFetcher(tmp_path, "test@example.test").fetch(DIRECTORY_URL) == b"{}"
    assert len(attempts) == 2 and attempts[1] - attempts[0] >= (2 if status == 429 else 1)
    assert len(list(tmp_path.glob("*.cache"))) == 1


@pytest.mark.parametrize(
    "status,headers",
    [
        (403, {}),
        (429, {"retry-after": "60"}),
        (429, {"retry-after": "Thu, 08 Oct 2026 12:00:00 GMT"}),
    ],
)
def test_fetcher_does_not_retry_forbidden_or_unbounded_cooldown(
    tmp_path, monkeypatch, status, headers
):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        return httpx.Response(status, headers=headers, request=httpx.Request("GET", url))

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", request)
    with pytest.raises(httpx.HTTPStatusError):
        SecFetcher(tmp_path, "test@example.test").fetch(DIRECTORY_URL)
    assert len(calls) == 1 and not list(tmp_path.glob("*.cache"))


def test_network_calls_can_overlap_outside_sec_pacing_lock(tmp_path, monkeypatch):
    entered = []
    both, release = threading.Event(), threading.Event()

    def request(url, **kwargs):
        entered.append(url)
        if len(entered) == 2:
            both.set()
        assert release.wait(3)
        return httpx.Response(200, content=b"{}", request=httpx.Request("GET", url))

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", request)
    fetcher = SecFetcher(tmp_path, "test@example.test")
    threads = [
        threading.Thread(target=fetcher.fetch, args=(url,))
        for url in (DIRECTORY_URL, "https://data.sec.gov/submissions/CIK0001318605.json")
    ]
    for thread in threads:
        thread.start()
    try:
        assert both.wait(2)
    finally:
        release.set()
        for thread in threads:
            thread.join(3)


def test_direct_research_counts_name_conversion_model_usage(discovery_service):
    service = discovery_service

    class Gateway:
        calls = 0

        def complete(self, messages, tools, review=False):
            self.calls += 1
            message = {"role": "assistant", "content": None}
            if self.calls == 1:
                message["content"] = '{"name":"Tesla"}'
            else:
                if self.calls == 2:
                    name, args = (
                        "get_financial_fact",
                        {"ticker": "TSLA", "metric": "revenue", "fiscal_year": 2025},
                    )
                else:
                    evidence = service.tools(date(2026, 10, 8)).get_financial_fact(
                        "TSLA", "revenue", 2025
                    )["evidence"]
                    name, args = (
                        "submit_answer",
                        {
                            "claims": [{"kind": "fact", "evidence_ids": [evidence["id"]]}],
                            "gaps": [],
                        },
                    )
                message["tool_calls"] = [
                    {
                        "id": str(self.calls),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ]
            return {
                "model": "fixture",
                "message": message,
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }

    service.gateway = Gateway()
    answer = service.research(
        ResearchRequest(question="FY2025 营收", company="特斯拉", as_of="2026-10-08")
    )
    assert answer.status == "answered"
    # Only name conversion uses the model; the annual amount is code-rendered.
    assert answer.usage["model_calls"] == 1 and answer.usage["total_tokens"] == 10
    assert service.gateway.calls == 1
    assert [entry["stage"] for entry in answer.trace[:2]] == [
        "company_lookup",
        "annual_numeric_path",
    ]
