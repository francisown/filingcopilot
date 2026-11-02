"""Durable research job/event records across process restarts."""

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.corpus import Corpus
from filingcopilot.execution import progress, research_scope
from filingcopilot.jobs import CorpusJobStore, ResearchJobs
from filingcopilot.models import ResearchAnswer, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def request():
    return ResearchRequest(
        language="zh", question="苹果 FY2025 营收", ticker="AAPL", mode="reference"
    )


def answer(req):
    return ResearchAnswer(
        id="test-result",
        question=req.question,
        as_of=req.as_of,
        mode=req.mode,
        status="answered",
        answer=[],
        gaps=[],
        evidence=[],
        trace=[],
        usage={"input_tokens": 10, "output_tokens": 5},
        elapsed_ms=0,
    )


class Service:
    def __init__(self, corpus):
        self.corpus = corpus
        self.settings = SimpleNamespace(
            max_cost_usd=None,
            model_input_price_per_million=None,
            model_output_price_per_million=None,
        )
        self.count = 0

    def research_plan(self, req):
        return {"ready": True}

    def research(self, req, control):
        self.count += 1
        with research_scope(control):
            progress("model_request", "正在处理。")
        return control.finalize(answer(req))


def _running_row(req, key, *, heartbeat_at, owner):
    now = time.time()
    return {
        "id": "job-" + key,
        "request_key": key,
        "owner": owner,
        "status": "running",
        "started_at": now - 600,
        "finished_at": None,
        "elapsed_ms": None,
        "heartbeat_at": heartbeat_at,
        "request": req.model_dump(mode="json"),
        "result": None,
        "preview": None,
        "error": None,
        "event_sequence": 0,
    }


def test_completed_job_and_events_survive_restart(tmp_path):
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    first = ResearchJobs(service, workers=1)
    try:
        job = first.start(request(), "durable-complete-0001")
        first.jobs[job["id"]]["future"].result(5)
        assert service.count == 1
    finally:
        first.close()

    second = ResearchJobs(service, workers=1)
    try:
        loaded = second.get(job["id"])
        assert loaded["status"] == "completed"
        assert loaded["result"]["id"] == "test-result"
        assert loaded["usage"] == {"input_tokens": 10, "output_tokens": 5}
        assert any(event["stage"] == "model_request" for event in loaded["events"])

        # A lost response retry must return the persisted job, not spend again.
        replayed = second.start(request(), "durable-complete-0001")
        assert replayed["id"] == job["id"]
        assert service.count == 1
        assert second.recover("durable-complete-0001")["id"] == job["id"]
    finally:
        second.close()


def test_dead_process_running_job_becomes_interrupted(tmp_path):
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    store = CorpusJobStore(corpus)
    now = time.time()
    store.save(
        _running_row(
            request(), "stale-running-0001", heartbeat_at=now - 600, owner="dead-process"
        )
    )

    jobs = ResearchJobs(service, workers=1)
    try:
        loaded = jobs.get("job-stale-running-0001")
        assert loaded["status"] == "interrupted"
        assert loaded["error"]["type"] == "ProcessRestart"
        assert "研究记录" in loaded["error"]["message"]
        assert jobs.recover("stale-running-0001")["status"] == "interrupted"
    finally:
        jobs.close()


def test_concurrent_duplicate_key_resolves_to_the_inserted_row(tmp_path, monkeypatch):
    """A lost lookup race must not spend a second run or return a 500."""
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    CorpusJobStore(corpus).save(
        _running_row(
            request(), "race-duplicate-0001", heartbeat_at=time.time(), owner="other-process"
        )
    )

    jobs = ResearchJobs(service, workers=1)
    original = jobs.store.find_key
    calls = {"count": 0}

    def miss_first_lookups(key):
        calls["count"] += 1
        if calls["count"] <= 2:
            return None
        return original(key)

    monkeypatch.setattr(jobs.store, "find_key", miss_first_lookups)
    try:
        found = jobs.start(request(), "race-duplicate-0001")
        assert found["id"] == "job-race-duplicate-0001"
        assert found["status"] == "running"
        assert service.count == 0
    finally:
        jobs.close()


def test_heartbeat_is_lazy_and_stops_with_the_job_store(tmp_path):
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    jobs = ResearchJobs(service, workers=1)
    try:
        assert jobs._heartbeat_thread is None
        job = jobs.start(request(), "lazy-heartbeat-0001")
        jobs.jobs[job["id"]]["future"].result(5)
        assert jobs._heartbeat_thread is not None
        assert jobs._heartbeat_thread.is_alive()
    finally:
        jobs.close()
    assert jobs._heartbeat_thread is None


def test_fresh_sibling_heartbeat_is_not_interrupted(tmp_path):
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    store = CorpusJobStore(corpus)
    store.save(
        _running_row(
            request(), "live-sibling-0001", heartbeat_at=time.time(), owner="other-process"
        )
    )

    jobs = ResearchJobs(service, workers=1)
    try:
        assert jobs.get("job-live-sibling-0001")["status"] == "running"
    finally:
        jobs.close()


def test_capacity_counts_persisted_live_jobs(tmp_path):
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    store = CorpusJobStore(corpus)
    store.save(
        _running_row(
            request(), "live-capacity-0001", heartbeat_at=time.time(), owner="other-process"
        )
    )

    jobs = ResearchJobs(service, workers=1, capacity=1)
    try:
        with pytest.raises(RuntimeError, match="队列已满"):
            jobs.start(request(), "new-capacity-0001")
        assert service.count == 0
    finally:
        jobs.close()


def test_persisted_history_is_bounded_and_old_keys_expire(tmp_path):
    corpus = Corpus(tmp_path)
    service = Service(corpus)
    jobs = ResearchJobs(service, workers=1, capacity=1, history=2)
    try:
        ids = []
        for index in range(6):
            key = f"bounded-durable-{index:04d}"
            job = jobs.start(request(), key)
            jobs.jobs[job["id"]]["future"].result(5)
            ids.append(job["id"])
        with pytest.raises(ValueError, match="进度记录已过期"):
            jobs.recover("bounded-durable-0000")
        with corpus.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM research_jobs").fetchone()[0] <= 3
            assert db.execute("SELECT COUNT(*) FROM research_events").fetchone()[0] <= 3 * 500
    finally:
        jobs.close()


def test_api_recovers_completed_job_in_a_new_process(tmp_path):
    first_service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    first_app = create_app(first_service)
    with TestClient(first_app) as client:
        started = client.post(
            "/api/research/jobs",
            json=request().model_dump(mode="json"),
            headers={"Idempotency-Key": "api-durable-0001"},
        )
        assert started.status_code == 202
        job_id = started.json()["id"]
        first_app.state.research_jobs.jobs[job_id]["future"].result(5)
        expected = first_app.state.research_jobs.get(job_id)["result"]["id"]

    second_service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    second_app = create_app(second_service)
    with TestClient(second_app) as client:
        recovered = client.get("/api/research/jobs/recover?key=api-durable-0001")
        assert recovered.status_code == 200
        assert recovered.json()["id"] == job_id
        assert recovered.json()["status"] == "completed"
        loaded = client.get("/api/research/jobs/" + job_id)
        assert loaded.status_code == 200
        assert loaded.json()["result"]["id"] == expected
