import httpx
import pytest
from test_gateway import response

from filingcopilot.agent import OpenAIGateway
from filingcopilot.execution import ResearchControl
from filingcopilot.models import ResearchAnswer, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_whole_research_budget_stops_retry_and_saves_result(tmp_path, monkeypatch):
    """Replay a long server cooldown at the public service/gateway boundary."""
    now = [100.0]
    requests = []
    settings = Settings(
        home=tmp_path,
        api_key="test",
        model="test",
        embedding_mode="off",
        min_model_interval=0,
        research_timeout=2,
    )
    service = FilingCopilot(settings)
    control = ResearchControl()
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])

    def fake_wait(seconds):
        now[0] += seconds
        return False

    monkeypatch.setattr(control._cancel, "wait", fake_wait)

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                429, headers={"retry-after": "30"}, json={"error": {"message": "temporary"}}
            )
        return response()

    gateway = OpenAIGateway(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    gateway.clock = lambda: now[0]

    def research(request):
        gateway.complete([], None)
        answer = ResearchAnswer(
            id="deadline-replay",
            question=request.question,
            as_of=request.as_of,
            mode="agent",
            status="abstained",
            answer=[],
            gaps=[],
            evidence=[],
            trace=[],
            usage={},
            elapsed_ms=0,
        )
        return answer, service.refresh_domain(), "AAPL"

    monkeypatch.setattr(service, "_research", research)
    result = service.research(
        ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL"), control=control
    )
    assert len(requests) == 1, "research issued another HTTP request after its whole-round budget"
    assert now[0] <= 102.0
    assert result.status == "abstained"
    assert any("时间预算" in gap for gap in result.gaps)
    assert service.export_package(result.id)["research"]["id"] == result.id


def fake_time(monkeypatch, control):
    now = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])

    def advance(seconds):
        now[0] += seconds
        return False

    monkeypatch.setattr(control._cancel, "wait", advance)
    return now


def test_rate_slot_wait_consumes_same_budget_without_http(tmp_path, monkeypatch):
    import pytest

    from filingcopilot.execution import ResearchDeadlineExceeded, research_scope

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(2)
    requests = []
    gateway = OpenAIGateway(
        Settings(home=tmp_path, api_key="test", model="test", min_model_interval=0),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: requests.append(r) or response())
        ),
    )
    gateway.clock = lambda: now[0]
    gateway.next_call["test"] = 160
    with research_scope(control), pytest.raises(ResearchDeadlineExceeded):
        gateway.complete([], None)
    assert requests == [] and now[0] == 102


def test_request_timeout_recomputed_after_model_pacing(tmp_path, monkeypatch):
    from filingcopilot.execution import research_scope

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(2)
    timeouts = []
    gateway = OpenAIGateway(
        Settings(home=tmp_path, api_key="test", model="test", min_model_interval=0),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: timeouts.append(r.extensions["timeout"]) or response()
            )
        ),
    )
    gateway.clock = lambda: now[0]
    gateway.next_call["test"] = 101.5
    with research_scope(control):
        assert gateway.complete([], None)["message"]["content"] == "ok"
    assert timeouts == [{"connect": 0.5, "read": 0.5, "write": 0.5, "pool": 0.5}]


def test_expired_review_keeps_only_completed_batch_no_repairs(tools, monkeypatch):
    from test_research import ScriptedGateway

    from filingcopilot.agent import ResearchAgent
    from filingcopilot.execution import research_scope

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(2)
    claims = [
        {"kind": "narrative", "statement": s, "evidence_ids": ["p1"]}
        for s in ["公司依赖制造合作伙伴。", "供应中断可能影响运营。", "没有发生违约。"]
    ]

    class Gateway(ScriptedGateway):
        def complete(self, messages, schemas, review=False):
            result = super().complete(messages, schemas, review)
            if review:
                now[0] = 102.0  # First in-flight review completed at the boundary.
            return result

    gateway = Gateway(
        [
            [("search_passages", {"query": "supply", "ticker": "AAPL"})],
            [("submit_answer", {"claims": claims, "gaps": []})],
            [
                (
                    "submit_review",
                    {
                        "verdicts": [
                            {"index": i, "supported": True, "reason": "controlled completed batch"}
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
    assert len(result.answer) == 2 and result.usage["model_calls"] == 3
    assert result.status == "partial" and any("时间预算" in g for g in result.gaps)
    assert all(c["text"] != claims[2]["statement"] for c in result.answer)
    assert not any(t["stage"] == "reason" for t in result.trace[5:])
    assert result.trace[-2]["error_type"] == "ResearchDeadlineExceeded"


def test_late_research_response_does_not_execute_model_tools(tools, monkeypatch):
    from test_research import ScriptedGateway

    from filingcopilot.agent import ResearchAgent
    from filingcopilot.execution import research_scope

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(2)

    class Gateway(ScriptedGateway):
        def complete(self, *args, **kwargs):
            result = super().complete(*args, **kwargs)
            now[0] = 102
            return result

    gateway = Gateway(
        [[("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})]]
    )
    with research_scope(control):
        result = ResearchAgent(Settings(home=tools.corpus.home), gateway).run(
            ResearchRequest(language="zh", question="苹果营收", ticker="AAPL"), tools
        )
    assert not result.answer and not tools.ledger
    assert result.usage["model_calls"] == 1
    assert any("时间预算" in g for g in result.gaps)
    assert not any("不足以支持" in g for g in result.gaps)


def test_expired_stock_keeps_deterministic_figures_without_unreviewed_opinion(tools):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.settings import Settings

    agent = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off"), gateway=None)
    overview = tools.get_company_overview("AAPL")
    state = {"verified": []}
    retained = agent._deadline_numeric_fallback(
        state, tools, "苹果股票怎么样？", "AAPL", overview, None
    )
    assert retained
    assert all(item["kind"] in {"fact", "calculation", "valuation", "market"} for item in retained)
    assert not any(item["kind"] in {"analysis", "narrative"} for item in retained)


def test_full_report_deadline_keeps_local_business_material(tools):
    """A timed-out six-section run still returns auditable local evidence."""
    import time

    service = FilingCopilot(Settings(home=tools.corpus.home, embedding_mode="off"))
    request = ResearchRequest(
        question="完整公司股票研究：商业模式、增长、现金流、竞争与优势、风险和估值条件",
        ticker="AAPL",
        depth="full",
        mode="agent",
    )
    answer, _domain, focus = service._deadline_company_fallback(
        request, ResearchControl(), time.perf_counter()
    )
    assert focus == "AAPL"
    assert answer.mode == "company_report"
    assert answer.status in {"partial", "abstained"}
    assert answer.gaps
    assert all(claim["kind"] in {"fact", "calculation", "excerpt"} for claim in answer.answer)


def test_budget_never_renews_and_finalization_freezes_stop_state(tmp_path, monkeypatch):
    from filingcopilot.models import ResearchAnswer

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(2)
    now[0] = 101
    control.start_budget(200)
    assert control.remaining == 1
    req = ResearchRequest(language="zh", question="查询", ticker="AAPL")
    answer = ResearchAnswer(
        id="sealed",
        question=req.question,
        as_of=req.as_of,
        mode="reference",
        status="answered",
        answer=[],
        gaps=[],
        evidence=[],
        trace=[],
        usage={},
        elapsed_ms=0,
    )
    control.finalize(answer)
    now[0] = 1000
    assert not control.expired and not control.cancel() and not answer.gaps


def test_prestart_expiry_saves_partial_state_without_calling_sources(tmp_path, monkeypatch):
    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(2)
    now[0] = 102
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    monkeypatch.setattr(
        service.discovery,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("source called")),
    )
    answer = service.research(ResearchRequest(language="zh", question="苹果股票怎么样", ticker="AAPL"), control)
    assert answer.trace[-1]["stage"] == "deadline" and answer.trace[-1]["focus_ticker"] == "AAPL"
    assert not any("取消" in g for g in answer.gaps)
    assert not control.cancel()  # Expiry must not be relabelled as user cancellation.
    assert service.export_package(answer.id)["research"]["status"] == "abstained"


def test_queue_time_does_not_use_next_job_budget(tmp_path, monkeypatch):
    import threading

    from filingcopilot.jobs import ResearchJobs

    now = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off", research_timeout=2))
    calls = []

    def research(request):
        calls.append(request.question)
        if request.question == "first":
            entered.set()
            assert release.wait(2)
        answer = ResearchAnswer(
            id=request.question,
            question=request.question,
            as_of=request.as_of,
            mode="reference",
            status="answered",
            answer=[],
            gaps=[],
            evidence=[],
            trace=[],
            usage={},
            elapsed_ms=0,
        )
        return answer, service.refresh_domain(), "AAPL"

    monkeypatch.setattr(service, "_research", research)
    jobs = ResearchJobs(service, workers=1)
    try:
        first = jobs.start(ResearchRequest(language="zh", question="first", ticker="AAPL", mode="reference"))
        assert entered.wait(1)
        second = jobs.start(ResearchRequest(language="zh", question="second", ticker="AAPL", mode="reference"))
        assert jobs.get(second["id"])["remaining_seconds"] is None
        now[0] = 110
        assert jobs.get(first["id"])["status"] == "timing_out"
        release.set()
        jobs.jobs[second["id"]]["future"].result(3)
        assert jobs.get(first["id"])["status"] == "timed_out"
        assert jobs.get(second["id"])["status"] == "completed"
        assert jobs.get(second["id"])["time_budget_seconds"] == 2
        assert calls == ["first", "second"]
    finally:
        release.set()
        jobs.close()


def test_lock_wait_and_sec_backoff_share_budget(tmp_path, monkeypatch):
    import pytest

    from filingcopilot.execution import ResearchDeadlineExceeded, cancellable_lock, research_scope
    from filingcopilot.ingestion import SecFetcher

    class BusyLock:
        def acquire(self, timeout):
            now[0] += timeout
            return False

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(0.5)
    with (
        research_scope(control),
        pytest.raises(ResearchDeadlineExceeded),
        cancellable_lock(BusyLock()),
    ):
        raise AssertionError("acquired")
    assert now[0] == 100.5
    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    control.start_budget(0.5)
    requests = []

    def get(url, **kwargs):
        requests.append(kwargs["timeout"])
        return httpx.Response(502, request=httpx.Request("GET", url))

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    # The shared SEC limiter must use this test clock, not a previous real request.
    monkeypatch.setattr(SecFetcher, "_last_request", 0.0)
    fetcher = SecFetcher(tmp_path, "Test test@example.com")
    with research_scope(control), pytest.raises(ResearchDeadlineExceeded):
        fetcher.fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert requests == [0.5]


def test_preparation_deadline_retains_safe_diagnostics_without_exception_text(
    tmp_path, monkeypatch
):
    from filingcopilot.execution import progress, run_step

    control = ResearchControl()
    now = fake_time(monkeypatch, control)
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off", research_timeout=2))

    def incomplete(request):
        progress("sec_retry", "SEC 等待重试。", error_type="ReadTimeout", status=None, attempt=1)

        def fetch():
            now[0] = 102

        run_step("quarterly_preparation", "正在准备季报。", fetch)
        raise AssertionError("must stop before continuing")

    monkeypatch.setattr(service, "_research", incomplete)
    answer = service.research(ResearchRequest(language="zh", question="苹果股票怎么样", ticker="AAPL"), control)
    events = next(t for t in answer.trace if t["stage"] == "execution_events")
    assert events["events"][-1]["source_stage"] == "quarterly_preparation"
    assert events["events"][-1]["error_type"] == "ResearchDeadlineExceeded"
    failures = [event for event in events["events"] if event.get("error_type")]
    assert failures[0]["error_type"] == "ReadTimeout"
    assert answer.research_type == "stock" and answer.trace[-1]["stage"] == "deadline"
    assert "secret" not in str(service.export_package(answer.id))


def test_diagnostics_ring_is_bounded_and_omits_raw_exception(tmp_path, monkeypatch):
    import pytest

    from filingcopilot.execution import research_scope, run_step

    control = ResearchControl()
    fake_time(monkeypatch, control)
    control.start_budget(2)
    for i in range(105):
        control.report("probe", "controlled event", index=i)
    assert len(control.events) == 100 and control.event_count == 105
    assert control.events[0]["index"] == 5

    def fail():
        raise httpx.ConnectError("Authorization: Bearer secret-credential")

    with research_scope(control), pytest.raises(httpx.ConnectError):
        run_step("evidence", "读取资料。", fail)
    assert control.events[-1]["error_type"] == "ConnectError"
    assert "secret" not in str(control.events)


def test_deadlines_are_scoped_without_stopping_other_worker(tmp_path, monkeypatch):
    import pytest

    from filingcopilot.execution import ResearchDeadlineExceeded, checkpoint, research_scope

    one, two = ResearchControl(), ResearchControl()
    now = fake_time(monkeypatch, one)
    one.start_budget(1)
    two.start_budget(5)
    now[0] = 102
    with research_scope(one):
        with pytest.raises(ResearchDeadlineExceeded):
            checkpoint()
        with research_scope(two):
            checkpoint()
        with pytest.raises(ResearchDeadlineExceeded):
            checkpoint()
    checkpoint()
    assert not two.expired and two.remaining == 3
