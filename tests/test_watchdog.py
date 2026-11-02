"""A blocked worker must not keep a job running past its deterministic deadline."""

import threading
import time

import pytest

from filingcopilot.corpus import Corpus
from filingcopilot.execution import ResearchControl, progress, research_scope
from filingcopilot.jobs import ResearchJobs
from filingcopilot.models import ResearchAnswer, ResearchRequest

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def request():
    return ResearchRequest(language="zh", question="苹果 FY2025 营收", ticker="AAPL", mode="reference")


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
        usage={},
        elapsed_ms=0,
    )


class BlockingService:
    def __init__(self, budget=0.02):
        self.corpus = None
        self.budget = budget
        self.entered = threading.Event()
        self.release = threading.Event()
        self.count = 0

    def research_plan(self, req):
        return {"ready": True}

    def research(self, req, control):
        self.count += 1
        control.start_budget(self.budget)
        self.entered.set()
        with research_scope(control):
            progress("model_request", "正在等待模型返回。")
            self.release.wait(5)
        return control.finalize(answer(req))


def watchdog(tmp_path, service, *, capacity=1):
    service.corpus = Corpus(tmp_path)
    jobs = ResearchJobs(service, workers=1, capacity=capacity)
    jobs.watchdog_interval = 0.01
    jobs.watchdog_grace = 0.02
    return jobs


def test_watchdog_terminates_a_blocked_expired_job_and_releases_capacity(tmp_path):
    service = BlockingService()
    jobs = watchdog(tmp_path, service)
    try:
        first = jobs.start(request())
        assert service.entered.wait(1)
        deadline = time.monotonic() + 3
        while jobs.get(first["id"])["status"] != "timed_out":
            assert time.monotonic() < deadline
            time.sleep(0.02)

        view = jobs.get(first["id"])
        assert view["error"]["type"] == "ResearchWatchdog"
        assert any(event["stage"] == "watchdog_timeout" for event in view["events"])
        assert jobs.store.load(first["id"])["status"] == "timed_out"

        # The blocked worker no longer occupies a persisted queue slot.
        second = jobs.start(request())
        assert second["id"] != first["id"]

        # A late cooperative return keeps the watchdog's terminal outcome.
        service.release.set()
        jobs.jobs[first["id"]]["future"].result(5)
        late = jobs.get(first["id"])
        assert late["status"] == "timed_out"
        assert late["result"] is not None
    finally:
        service.release.set()
        jobs.close()


def test_watchdog_leaves_a_live_job_alone_and_exits_when_idle(tmp_path):
    service = BlockingService(budget=None)
    jobs = watchdog(tmp_path, service)
    try:
        job = jobs.start(request())
        assert service.entered.wait(1)
        time.sleep(0.1)
        assert jobs.get(job["id"])["status"] == "running"

        service.release.set()
        jobs.jobs[job["id"]]["future"].result(5)
        assert jobs.get(job["id"])["status"] == "completed"

        deadline = time.monotonic() + 2
        while jobs._watchdog_thread is not None:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        jobs.close()


def test_watchdog_does_not_override_a_requested_cancellation(tmp_path):
    service = BlockingService(budget=0.05)
    jobs = watchdog(tmp_path, service)
    try:
        job = jobs.start(request())
        assert service.entered.wait(1)
        jobs.cancel(job["id"])
        time.sleep(0.2)
        assert jobs.get(job["id"])["status"] == "cancelling"

        service.release.set()
        jobs.jobs[job["id"]]["future"].result(5)
        assert jobs.get(job["id"])["status"] == "cancelled"
    finally:
        jobs.close()


def test_control_exposes_the_monotonic_deadline_to_supervisors():
    control = ResearchControl()
    assert control.deadline is None
    control.start_budget(30)
    assert control.deadline is not None
    assert control.deadline > time.monotonic()
