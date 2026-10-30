"""Cached research must survive slow refreshes at the real service/SEC seam."""

import httpx
import pytest

from filingcopilot.execution import ResearchControl
from filingcopilot.ingestion import SecFetcher
from filingcopilot.models import Claim, ResearchAnswer, ResearchRequest, Submission
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.mark.parametrize("latency", [30.0, 0.0])
def test_failed_refresh_leaves_budget_for_cached_research(tmp_path, monkeypatch, latency):
    now = [100.0]
    requests, entered = [], []
    control = ResearchControl()
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    monkeypatch.setattr(SecFetcher, "_last_request", 0.0)
    monkeypatch.setattr(
        control._cancel, "wait", lambda seconds: now.__setitem__(0, now[0] + seconds) or False
    )

    def get(url, **kwargs):
        requests.append({"url": url, "timeout": kwargs["timeout"]})
        now[0] += min(latency, kwargs["timeout"])
        raise httpx.ReadTimeout("controlled slow SEC connection")

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    service = FilingCopilot(
        Settings(
            home=tmp_path,
            embedding_mode="off",
            sec_user_agent="test@example.test",
            research_timeout=300,
        ),
        gateway=object(),
    )

    def cached_research(agent, request, tools):
        entered.append(control.remaining)
        assert len(tools.refresh_status) == 3
        assert all(not item["checked"] and item["warnings"] for item in tools.refresh_status)
        result = tools.get_financial_fact("AAPL", "revenue", 2025)
        claims, errors = verify_submission(
            Submission(claims=[Claim(kind="fact", evidence_ids=[result["evidence"]["id"]])]),
            tools,
            request.question,
            focus_ticker="AAPL",
        )
        return ResearchAnswer(
            id="cached-refresh-replay",
            question=request.question,
            as_of=request.as_of,
            mode="agent",
            status="partial",
            answer=claims,
            gaps=errors,
            evidence=list(tools.ledger.values()),
            trace=[],
            usage={},
            elapsed_ms=0,
            research_type="stock",
        )

    monkeypatch.setattr("filingcopilot.service.ResearchAgent.run", cached_research)
    try:
        answer = service.research(
            ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL", as_of="2026-10-08"),
            control,
        )
        assert entered and entered[0] >= 180, (
            f"slow SEC refresh prevented cached research: remaining={entered}, "
            f"elapsed={now[0] - 100}, requests={len(requests)}, claims={len(answer.answer)}"
        )
        assert answer.answer and answer.status == "partial"
        assert any("检查未完成" in gap for gap in answer.gaps)
        assert not control.expired and not any(t["stage"] == "deadline" for t in answer.trace)
        assert not getattr(service.discovery, "quarter_checks", {})
        assert not getattr(service.discovery, "update_checks", {})
        if latency:
            assert len(requests) == 1, "the same failed source was fetched again during refresh"
        assert service.export_package(answer.id)["research"]["answer"] == answer.answer
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_refresh_budget_releases_scope_and_preserves_global_budget(monkeypatch):
    from filingcopilot.execution import (
        RefreshBudgetExceeded,
        bounded_timeout,
        checkpoint,
        refresh_scope,
        research_scope,
        wait,
    )

    now = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    control = ResearchControl()
    control.start_budget(100)
    monkeypatch.setattr(control._cancel, "wait", lambda s: now.__setitem__(0, now[0] + s) or False)
    with research_scope(control):
        with pytest.raises(RefreshBudgetExceeded), refresh_scope(5):
            assert bounded_timeout(30) == 5
            with refresh_scope(50):
                wait(60)
        checkpoint()
        assert control.remaining == 95 and not control.expired
        assert bounded_timeout(30) == 30


@pytest.mark.parametrize("manual", [False, True])
def test_global_stop_is_never_swallowed_by_source_fallback(tmp_path, monkeypatch, manual):
    from filingcopilot.execution import ResearchCancelled, ResearchDeadlineExceeded, research_scope

    now = [100.0]
    control = ResearchControl()
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    monkeypatch.setattr(SecFetcher, "_last_request", 0.0)
    control.start_budget(2)
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", sec_user_agent="test@example.test")
    )

    def get(url, **kwargs):
        if manual:
            control.cancel()
        else:
            now[0] += kwargs["timeout"]
        raise httpx.ConnectError("controlled stop")

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    try:
        with (
            research_scope(control),
            pytest.raises(ResearchCancelled if manual else ResearchDeadlineExceeded),
        ):
            service.discovery.refresh_annual("AAPL", __import__("datetime").date(2026, 10, 8))
        assert not service.discovery.fetcher._refresh_failures
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_failed_url_cooldown_expires_and_does_not_block_other_sources(tmp_path, monkeypatch):
    from filingcopilot.execution import refresh_scope
    from filingcopilot.ingestion import SecRefreshDeferred

    now = [100.0]
    calls = []
    monkeypatch.setattr("filingcopilot.ingestion.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "filingcopilot.execution.time.sleep", lambda s: now.__setitem__(0, now[0] + s)
    )
    monkeypatch.setattr(SecFetcher, "_last_request", 0.0)

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            403 if len(calls) == 1 else 200, content=b"{}", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    fetcher = SecFetcher(tmp_path, "test@example.test")
    one = "https://data.sec.gov/submissions/CIK0000320193.json"
    two = "https://data.sec.gov/submissions/CIK0000789019.json"
    with refresh_scope(20), pytest.raises(httpx.HTTPStatusError):
        fetcher.fetch(one, max_age=0)
    with refresh_scope(20):
        with pytest.raises(SecRefreshDeferred) as deferred:
            fetcher.fetch(one, max_age=0)
        assert deferred.value.status == 403 and len(calls) == 1
        assert fetcher.fetch(two) == b"{}" and len(calls) == 2
    now[0] += 61
    with refresh_scope(20):
        assert fetcher.fetch(one, max_age=0) == b"{}"
    assert calls == [one, two, one] and not fetcher._refresh_failures


def test_agent_receives_failed_refresh_context(tools):
    import json

    from test_research import ScriptedGateway

    from filingcopilot.agent import ResearchAgent

    seen = []

    class Gateway(ScriptedGateway):
        def complete(self, messages, schemas, review=False):
            seen.append(messages.copy())
            return super().complete(messages, schemas, review)

    tools.refresh_status = [
        {"stage": "annual_refresh", "checked": False, "warnings": ["检查未完成"]}
    ]
    gateway = Gateway(
        [
            [("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})],
            [("submit_answer", {"claims": [{"kind": "fact", "evidence_ids": ["rev25"]}]})],
        ]
    )
    answer = ResearchAgent(Settings(home=tools.corpus.home), gateway).run(
        ResearchRequest(language="zh", question="苹果 FY2025 营业收入", ticker="AAPL"), tools
    )
    assert answer.answer
    payload = json.loads(seen[0][1]["content"])
    assert payload["source_refresh"] == tools.refresh_status
    assert "latest AVAILABLE" in seen[0][0]["content"]


@pytest.mark.parametrize(
    "method,cache", [("refresh_annual", "quarter_checks"), ("prepare_updates", "update_checks")]
)
def test_late_successful_response_cannot_cache_completed_check(
    tmp_path, monkeypatch, method, cache
):
    from datetime import date

    now = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    monkeypatch.setattr(SecFetcher, "_last_request", 0.0)

    def get(url, **kwargs):
        now[0] += kwargs["timeout"]
        return httpx.Response(
            200,
            json={
                "cik": 320193,
                "tickers": ["AAPL"],
                "filings": {
                    "recent": {
                        key: []
                        for key in (
                            "form",
                            "filingDate",
                            "accessionNumber",
                            "reportDate",
                            "primaryDocument",
                        )
                    },
                    "files": [],
                },
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", sec_user_agent="test@example.test")
    )
    try:
        result = getattr(service.discovery, method)("AAPL", date(2026, 10, 8))
        assert result["checked"] is False
        assert not getattr(service.discovery, cache, {}), (
            "expired source check was committed as fresh success"
        )
    finally:
        service.discovery.executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "method,cache,key",
    [
        ("refresh_annual", "quarter_checks", ("AAPL", "2026-10-08", "10-K")),
        ("prepare_updates", "update_checks", ("AAPL", "2026-10-08")),
    ],
)
def test_cache_hit_never_renews_success_ttl(tmp_path, monkeypatch, method, cache, key):
    from datetime import date

    now, calls = [200.0], []
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    monkeypatch.setattr(SecFetcher, "_last_request", 0.0)

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200,
            json={
                "cik": 320193,
                "tickers": ["AAPL"],
                "filings": {
                    "recent": {
                        key: []
                        for key in (
                            "form",
                            "filingDate",
                            "accessionNumber",
                            "reportDate",
                            "primaryDocument",
                        )
                    },
                    "files": [],
                },
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("filingcopilot.ingestion.httpx.get", get)
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", sec_user_agent="test@example.test")
    )
    previous = {"ticker": "AAPL", "checked": True, "warnings": [], "warning": None}
    setattr(service.discovery, cache, {key: (100.0, previous)})
    try:
        assert getattr(service.discovery, method)("AAPL", date(2026, 10, 8)) == previous
        assert getattr(service.discovery, cache)[key][0] == 100.0 and not calls
        now[0] = 3700.0
        assert getattr(service.discovery, method)("AAPL", date(2026, 10, 8))["checked"] is True
        assert len(calls) == 1 and getattr(service.discovery, cache)[key][0] == 3700.0
    finally:
        service.discovery.executor.shutdown(wait=True)
