"""Watchlist scans report candidates; only an explicit policy may start jobs."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.corpus import Corpus
from filingcopilot.models import Filing
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.watchlist import Watchlist

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def filing(fid, *, ticker="AAPL", form="10-K", days_ago=1, period_end="2025-09-27"):
    return Filing(
        id=fid,
        ticker=ticker,
        company="Apple",
        cik="0000320193",
        accession="0000320193-26-000001",
        form=form,
        filed=date.today() - timedelta(days=days_ago),
        period_end=date.fromisoformat(period_end),
        source_url=f"https://www.sec.gov/Archives/{fid}.htm",
        content_sha256="a" * 64,
    )


class FakeJobs:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def start(self, request, key):
        self.calls.append((request, key))
        if self.error is not None:
            raise self.error
        return {"id": f"job-{len(self.calls)}"}


def make_watchlist(tmp_path, filings, *, tickers=("AAPL",), **overrides):
    corpus = Corpus(tmp_path)
    for item in filings:
        corpus.ingest(item, [], [])
    with corpus.connect() as db:
        for ticker in tickers:
            db.execute(
                "INSERT INTO watchlist(ticker,payload) VALUES (?,?)",
                (ticker, json.dumps({"ticker": ticker, "name": f"{ticker} Inc."})),
            )
    settings = Settings(home=tmp_path, embedding_mode="off", **overrides)
    watch = Watchlist(corpus, lambda *a, **k: {}, lambda ticker: {}, settings)
    return corpus, watch


def test_scan_without_enabled_policy_reports_candidates_only(tmp_path):
    corpus, watch = make_watchlist(
        tmp_path,
        [filing("AAPL:10k"), filing("MSFT:10k", ticker="MSFT")],
    )
    jobs = FakeJobs()

    result = watch.scan(jobs, as_of=date.today())

    assert result["policy"]["enabled"] is False
    assert [item["filing_id"] for item in result["candidates"]] == ["AAPL:10k"]
    assert result["started"] == []
    assert jobs.calls == []
    assert watch.triggers()["items"] == []


def test_enabled_scan_starts_bounded_jobs_with_stable_keys(tmp_path):
    _, watch = make_watchlist(
        tmp_path,
        [filing("AAPL:10k"), filing("AAPL:8k", form="8-K", days_ago=2)],
        watchlist_triggers_enabled=True,
        watchlist_trigger_max_per_scan=1,
    )
    jobs = FakeJobs()

    first = watch.scan(jobs, as_of=date.today())
    second = watch.scan(jobs, as_of=date.today())
    third = watch.scan(jobs, as_of=date.today())

    assert len(first["started"]) == 1
    assert len(second["started"]) == 1
    assert third["started"] == []
    assert len(jobs.calls) == 2
    started = first["started"] + second["started"]
    assert len({item["request_key"] for item in started}) == 2
    assert {item["filing_id"] for item in started} == {"AAPL:10k", "AAPL:8k"}
    request, key = jobs.calls[0]
    assert request.ticker == "AAPL"
    assert request.mode == "reference"
    assert request.language == "zh"
    assert request.as_of == date.today()
    assert "10-K" in request.question
    assert key == started[0]["request_key"]
    assert all(item["status"] == "queued" for item in watch.triggers()["items"])


def test_scan_ignores_unapproved_forms_and_old_filings(tmp_path):
    _, watch = make_watchlist(
        tmp_path,
        [filing("AAPL:old", days_ago=40), filing("AAPL:8k", form="8-K", days_ago=2)],
        watchlist_triggers_enabled=True,
        watchlist_trigger_forms=("10-K",),
        watchlist_trigger_lookback_days=30,
    )
    jobs = FakeJobs()

    result = watch.scan(jobs, as_of=date.today())

    assert result["candidates"] == []
    assert result["started"] == []
    assert jobs.calls == []


def test_queue_full_is_recorded_and_retryable(tmp_path):
    _, watch = make_watchlist(
        tmp_path, [filing("AAPL:10k")], watchlist_triggers_enabled=True
    )
    jobs = FakeJobs(RuntimeError("queue full"))

    first = watch.scan(jobs, as_of=date.today())
    assert first["started"] == []
    assert first["skipped"][0]["reason"] == "queue_full"
    assert watch.triggers()["items"][0]["status"] == "skipped"

    jobs.error = None
    second = watch.scan(jobs, as_of=date.today())
    assert len(second["started"]) == 1
    assert watch.triggers()["items"][0]["status"] == "queued"


def test_api_scan_reports_disabled_policy_without_starting_jobs(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.ingest(filing("AAPL:watchlist-api"), [], [])
    with service.corpus.connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO watchlist(ticker,payload) VALUES (?,?)",
            ("AAPL", json.dumps({"ticker": "AAPL", "name": "Apple"})),
        )
    app = create_app(service)
    try:
        with TestClient(app) as client:
            response = client.post("/api/watchlist/scan")
            assert response.status_code == 200
            payload = response.json()
            assert payload["policy"]["enabled"] is False
            assert any(
                item["filing_id"] == "AAPL:watchlist-api"
                for item in payload["candidates"]
            )
            assert payload["started"] == []
            assert client.get("/api/watchlist/triggers").json()["items"] == []
            assert client.get("/api/health").json()["watchlist_triggers"] is False
    finally:
        app.state.research_jobs.close()
