import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.agent import OpenAIGateway, ResearchAgent
from filingcopilot.api import create_app
from filingcopilot.execution import (
    ResearchCancelled,
    ResearchControl,
    cancellable_lock,
    checkpoint,
    progress,
    research_scope,
)
from filingcopilot.jobs import ResearchJobs
from filingcopilot.models import ResearchAnswer, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

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
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.count = 0

    def research_plan(self, req):
        return {"ready": True}

    def research(self, req, control):
        self.count += 1
        self.entered.set()
        with research_scope(control):
            progress("model_request", "正在等待模型返回。")
            assert self.release.wait(3)
        return control.finalize(answer(req))


def test_lost_start_acknowledgement_retry_does_not_start_a_second_research():
    service = BlockingService()
    app = create_app(service)
    headers = {"Idempotency-Key": "browser-lost-ack-test-0001"}
    try:
        with TestClient(app) as client:
            # The server accepted the POST, but its response never reached the browser.
            lost_response = client.post(
                "/api/research/jobs", json=request().model_dump(mode="json"), headers=headers
            )
            assert lost_response.status_code == 202
            assert service.entered.wait(1)
            recovered = client.post(
                "/api/research/jobs", json=request().model_dump(mode="json"), headers=headers
            )
            assert recovered.status_code == 202
            assert recovered.json()["id"] == lost_response.json()["id"]
            assert len(app.state.research_jobs.jobs) == 1
            assert service.count == 1
    finally:
        service.release.set()
        app.state.research_jobs.close()


def test_request_key_replay_bypasses_full_queue_and_changed_plan():
    service = BlockingService()
    jobs = ResearchJobs(service, workers=1, capacity=1)
    key = "full-queue-recovery-0001"
    try:
        first = jobs.start(request(), key)
        assert service.entered.wait(1)
        service.research_plan = lambda req: {"ready": False, "message": "changed capability"}
        assert jobs.start(request(), key)["id"] == first["id"]
        assert jobs.recover(key)["id"] == first["id"]
        assert service.count == 1
    finally:
        service.release.set()
        jobs.close()


def test_concurrent_identical_keys_start_one_research(thread_timeout):
    service = BlockingService()
    barrier = threading.Barrier(2)
    service.research_plan = lambda req: (barrier.wait(thread_timeout), {"ready": True})[1]
    jobs = ResearchJobs(service, capacity=1)
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [
                pool.submit(jobs.start, request(), "concurrent-start-test-0001") for _ in range(2)
            ]
            ids = [f.result(thread_timeout * 2)["id"] for f in futures]
        assert len(set(ids)) == 1
        assert service.entered.wait(thread_timeout)
        assert service.count == 1 and len(jobs.jobs) == 1
    finally:
        service.release.set()
        jobs.close()


@pytest.mark.parametrize(
    "change",
    [
        {"question": "苹果净利润"},
        {"ticker": "MSFT"},
        {"mode": "agent"},
        {"as_of": date(2025, 1, 1)},
        {"auto_acquire": True},
        {"company": "苹果"},
    ],
)
def test_key_bound_to_entire_request_and_api_recovery_is_read_only(change):
    service = BlockingService()
    app = create_app(service)
    jobs = app.state.research_jobs
    headers = {"Idempotency-Key": "complete-request-test-0001", "X-Research-Session": jobs.session}
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/research/jobs", json=request().model_dump(mode="json"), headers=headers
            )
            assert first.status_code == 202
            modified = request().model_copy(update=change).model_dump(mode="json")
            conflict = client.post("/api/research/jobs", json=modified, headers=headers)
            assert conflict.status_code == 409
            recovered = client.get(
                "/api/research/jobs/recover?key=complete-request-test-0001", headers=headers
            )
            assert recovered.status_code == 200 and recovered.json()["id"] == first.json()["id"]
            missing = client.get(
                "/api/research/jobs/recover?key=00000000-0000-0000-0000-000000000000", headers=headers
            )
            assert missing.status_code == 404 and "will not resubmit" in missing.json()["detail"]
            assert len(jobs.jobs) == 1
    finally:
        service.release.set()
        jobs.close()


def test_server_restart_session_rejects_old_starts_and_recovery():
    service = BlockingService()
    app = create_app(service)
    headers = {
        "Idempotency-Key": "old-session-request-0001",
        "X-Research-Session": "previous-process",
    }
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/research/jobs", json=request().model_dump(mode="json"), headers=headers
            ).status_code
            == 409
        )
        assert (
            client.get(
                "/api/research/jobs/recover?key=old-session-request-0001", headers=headers
            ).status_code
            == 409
        )
        assert len(app.state.research_jobs.jobs) == 0 and service.count == 0


@pytest.mark.parametrize("key", ["", "short", "a" * 81, "a" * 16 + "/"])
def test_invalid_start_keys_rejected_without_work(key):
    service = BlockingService()
    app = create_app(service)
    with TestClient(app) as client:
        response = client.post(
            "/api/research/jobs",
            json=request().model_dump(mode="json"),
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422
        assert len(app.state.research_jobs.jobs) == 0


def test_completed_key_replay_retains_result_and_eviction_stays_bounded():
    service = BlockingService()
    service.release.set()
    jobs = ResearchJobs(service, capacity=1, history=2)
    try:
        ids = []
        for index in range(6):
            key = f"bounded-key-test-{index:04d}"
            job = jobs.start(request(), key)
            jobs.jobs[job["id"]]["future"].result(2)
            assert jobs.start(request(), key)["id"] == job["id"]
            ids.append(job["id"])
        assert len(jobs.jobs) == 3 and service.count == 6
        with pytest.raises(ValueError, match="进度记录已过期"):
            jobs.recover("bounded-key-test-0000")
        assert len(jobs.jobs) == 3 and service.count == 6
    finally:
        jobs.close()


def test_cancelled_key_replay_does_not_requeue_or_call_service():
    service = BlockingService()
    jobs = ResearchJobs(service, workers=1, capacity=2)
    try:
        jobs.start(request())
        assert service.entered.wait(1)
        queued = jobs.start(request(), "cancelled-request-test-0001")
        jobs.cancel(queued["id"])
        replay = jobs.start(request(), "cancelled-request-test-0001")
        assert replay["id"] == queued["id"] and replay["status"] == "cancelled"
        assert service.count == 1
        with pytest.raises(ValueError, match="标识格式"):
            jobs.start(request(), "公司请求" * 5)
    finally:
        service.release.set()
        jobs.close()


def test_queue_bound_and_queued_cancel_never_calls_service():
    service = BlockingService()
    jobs = ResearchJobs(service, workers=1, capacity=2)
    try:
        first = jobs.start(request())
        assert service.entered.wait(1)
        queued = jobs.start(request())
        with pytest.raises(RuntimeError, match="队列已满"):
            jobs.start(request())
        assert jobs.cancel(queued["id"])["status"] == "cancelled"
        assert jobs.cancel(queued["id"])["status"] == "cancelled"
        service.release.set()
        jobs.jobs[first["id"]]["future"].result(2)
        assert jobs.get(first["id"])["status"] == "completed"
        assert service.count == 1
    finally:
        service.release.set()
        jobs.close()


def test_inflight_cancel_acknowledges_wait_and_keeps_terminal_result():
    service = BlockingService()
    jobs = ResearchJobs(service)
    try:
        job = jobs.start(request())
        assert service.entered.wait(1)
        cancelling = jobs.cancel(job["id"])
        assert cancelling["status"] == "cancelling"
        assert cancelling["result"] is None
        assert "等待" in cancelling["events"][-1]["message"]
        service.release.set()
        jobs.jobs[job["id"]]["future"].result(2)
        terminal = jobs.get(job["id"])
        assert terminal["status"] == "cancelled"
        assert terminal["result"]["status"] == "abstained"
        assert "已取消" in terminal["result"]["gaps"][0]
        assert jobs.cancel(job["id"]) == terminal
    finally:
        service.release.set()
        jobs.close()


def test_completed_result_cannot_be_relabelled_by_late_cancel():
    service = BlockingService()
    service.release.set()
    jobs = ResearchJobs(service)
    try:
        job = jobs.start(request())
        jobs.jobs[job["id"]]["future"].result(2)
        terminal = jobs.get(job["id"])
        assert terminal["status"] == "completed"
        assert jobs.cancel(job["id"]) == terminal
    finally:
        jobs.close()


def test_cancel_after_result_commit_is_noop_even_before_worker_returns():
    sealed, release = threading.Event(), threading.Event()

    class CommittingService(BlockingService):
        def research(self, req, control):
            result = control.finalize(answer(req))
            sealed.set()
            assert release.wait(2)
            return result

    jobs = ResearchJobs(CommittingService())
    try:
        job = jobs.start(request())
        assert sealed.wait(1)
        assert jobs.cancel(job["id"])["status"] == "running"
        release.set()
        jobs.jobs[job["id"]]["future"].result(2)
        result = jobs.get(job["id"])
        assert result["status"] == "completed" and not result["result"]["gaps"]
    finally:
        release.set()
        jobs.close()


def test_job_failures_hide_provider_credentials():
    class Failing(BlockingService):
        def research(self, req, control):
            raise httpx.ConnectError("Authorization: Bearer secret-test-credential")

    jobs = ResearchJobs(Failing())
    try:
        job = jobs.start(request())
        jobs.jobs[job["id"]]["future"].result(2)
        terminal = jobs.get(job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["type"] == "ConnectError"
        assert "secret-test" not in json.dumps(terminal)
    finally:
        jobs.close()


def test_history_is_bounded_and_evicted_job_has_actionable_error():
    service = BlockingService()
    service.release.set()
    jobs = ResearchJobs(service, capacity=1, history=2)
    try:
        ids = []
        for _ in range(7):
            job = jobs.start(request())
            ids.append(job["id"])
            jobs.jobs[job["id"]]["future"].result(2)
        assert len(jobs.jobs) <= 3
        with pytest.raises(ValueError, match="研究记录"):
            jobs.get(ids[0])
    finally:
        jobs.close()


def test_controls_are_isolated_between_workers():
    one, two = ResearchControl(), ResearchControl()
    one.cancel()

    def check(control):
        with research_scope(control):
            try:
                checkpoint()
                return "active"
            except ResearchCancelled:
                return "cancelled"

    with ThreadPoolExecutor(2) as pool:
        assert pool.submit(check, one).result() == "cancelled"
        assert pool.submit(check, two).result() == "active"
    checkpoint()  # no leaked worker context


def test_cancel_while_waiting_for_company_lock_does_not_wait_for_other_research():
    lock, waiting = threading.Lock(), threading.Event()
    lock.acquire()
    control = ResearchControl(lambda e: waiting.set())

    def acquire():
        with research_scope(control), cancellable_lock(lock):
            pytest.fail("Cancelled worker acquired the lock")

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(acquire)
            assert waiting.wait(1)
            control.cancel()
            with pytest.raises(ResearchCancelled):
                future.result(1)
    finally:
        lock.release()


def test_cancel_after_sec_request_blocks_further_fetches(tmp_path, monkeypatch):
    from filingcopilot.ingestion import SecFetcher

    requests = []
    control = ResearchControl()

    def get(url, **kwargs):
        requests.append(url)
        control.cancel()
        return httpx.Response(200, content=b"{}", request=httpx.Request("GET", url))

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    fetcher = SecFetcher(tmp_path, "Test test@example.com")
    with research_scope(control):
        assert fetcher.fetch("https://data.sec.gov/submissions/CIK0000320193.json") == b"{}"
        with pytest.raises(ResearchCancelled):
            fetcher.fetch("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    assert len(requests) == 1


def test_cancel_during_gateway_pacing_prevents_http_request(tmp_path):
    from test_gateway import response

    requests = []
    waiting = threading.Event()
    gateway = OpenAIGateway(
        Settings(home=tmp_path, api_key="test", model="test", min_model_interval=60),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: requests.append(r) or response())
        ),
    )
    gateway.complete([], None)
    control = ResearchControl(lambda e: waiting.set() if e["stage"] == "model_wait" else None)

    def second():
        with research_scope(control):
            return gateway.complete([], None)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(second)
        assert waiting.wait(1)
        control.cancel()
        with pytest.raises(ResearchCancelled):
            future.result(1)
    assert len(requests) == 1


def test_cancel_during_retry_prevents_extra_http_request(tmp_path):
    requests = []
    waiting = threading.Event()
    gateway = OpenAIGateway(
        Settings(home=tmp_path, api_key="test", model="test", min_model_interval=0),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: (
                    requests.append(r)
                    or httpx.Response(502, json={"error": {"message": "temporary"}})
                )
            )
        ),
    )
    control = ResearchControl(lambda e: waiting.set() if e["stage"] == "model_retry" else None)

    def run():
        with research_scope(control):
            gateway.complete([], None)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run)
        assert waiting.wait(1)
        control.cancel()
        with pytest.raises(ResearchCancelled):
            future.result(1)
    assert len(requests) == 1


def test_cancel_after_model_return_does_not_execute_its_tools(tools):
    from test_research import ScriptedGateway

    control, events = ResearchControl(), []

    class Gateway(ScriptedGateway):
        def complete(self, *args, **kwargs):
            result = super().complete(*args, **kwargs)
            control.cancel()
            return result

    gateway = Gateway(
        [[("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})]]
    )
    control._publish = events.append
    with research_scope(control):
        result = ResearchAgent(Settings(home=tools.corpus.home), gateway).run(request(), tools)
    assert not result.answer and not tools.ledger
    assert "当前语料与知悉日期不足以支持回答。" not in result.gaps
    assert result.usage["model_calls"] == 1
    assert any(e["stage"] == "reason" for e in events)


def test_cancel_after_first_review_batch_retains_only_reviewed_claims(tools):
    from test_research import ScriptedGateway

    control = ResearchControl()
    claims = [
        {"kind": "narrative", "statement": s, "evidence_ids": ["p1"]}
        for s in ["公司依赖制造合作伙伴。", "供应中断可能影响运营。", "没有发生违约。"]
    ]

    class Gateway(ScriptedGateway):
        def complete(self, messages, tools, review=False):
            if review:
                control.cancel()
            return super().complete(messages, tools, review)

    gateway = Gateway(
        [
            [("search_passages", {"query": "supply", "ticker": "AAPL"})],
            [("submit_answer", {"claims": claims, "gaps": []})],
            [
                (
                    "submit_review",
                    {
                        "verdicts": [
                            {"index": i, "supported": True, "reason": "source supports"}
                            for i in [0, 1]
                        ]
                    },
                )
            ],
        ]
    )
    with research_scope(control):
        result = ResearchAgent(
            Settings(home=tools.corpus.home, review_batch_size=2), gateway
        ).run(
            ResearchRequest(language="zh", question="苹果供应风险", ticker="AAPL"), tools
        )
    assert result.usage["model_calls"] == 3
    assert len(result.answer) == 2
    assert all(c["text"] != claims[2]["statement"] for c in result.answer)
    assert result.status == "partial"


def test_service_prestart_cancellation_saves_auditable_empty_result(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    control = ResearchControl()
    control.cancel()
    result = service.research(request(), control=control)
    assert result.status == "abstained" and not result.answer
    assert result.usage["model_calls"] == 0
    assert result.trace[-1]["stage"] == "cancelled"
    assert service.export_package(result.id)["research"]["id"] == result.id


def test_api_background_completion_and_original_api_compatibility(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    app = create_app(service)
    with TestClient(app) as client:
        job = client.post("/api/research/jobs", json=request().model_dump(mode="json"))
        assert job.status_code == 202
        job_id = job.json()["id"]
        app.state.research_jobs.jobs[job_id]["future"].result(3)
        terminal = client.get("/api/research/jobs/" + job_id).json()
        assert terminal["status"] == "completed"
        assert terminal["result"]["usage"]["model_calls"] == 0
        assert any(e["stage"] == "saved" for e in terminal["events"])
        assert (
            client.post("/api/research/jobs/" + job_id + "/cancel").json()["status"] == "completed"
        )
        assert client.get("/api/research/jobs/missing").status_code == 404
        assert (
            client.post("/api/research", json=request().model_dump(mode="json")).status_code == 200
        )
        assert client.get("/api/health").json()["research_jobs"] is True
        assert client.get("/api/health").json()["as_of_default"] == date.today().isoformat()
        future = request().model_copy(update={"as_of": date(2099, 1, 1)})
        plan = client.post("/api/research/plan", json=future.model_dump(mode="json")).json()
        assert not plan["ready"] and date.today().isoformat() in plan["message"]
        assert (
            client.post("/api/research/jobs", json=future.model_dump(mode="json")).status_code
            == 422
        )
