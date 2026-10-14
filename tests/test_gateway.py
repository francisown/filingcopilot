import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from filingcopilot.agent import OpenAIGateway, ResearchAgent
from filingcopilot.models import ResearchRequest
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def response():
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def gateway(tmp_path, handler):
    return OpenAIGateway(
        Settings(home=tmp_path, api_key="test", model="test", min_model_interval=0),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_slow_http_does_not_hold_global_lock(tmp_path, thread_timeout):
    entered, release, concurrent = threading.Event(), threading.Event(), threading.Event()
    counter_lock = threading.Lock()
    count = 0

    def handler(request):
        nonlocal count
        with counter_lock:
            count += 1
            first = count == 1
        if first:
            entered.set()
            assert release.wait(thread_timeout * 2)
        else:
            concurrent.set()
        return response()

    client = gateway(tmp_path, handler)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.complete, [], None)
        assert entered.wait(thread_timeout)
        second = pool.submit(client.complete, [], None)
        try:
            assert concurrent.wait(thread_timeout), "HTTP request held the global lock"
        finally:
            release.set()
        assert first.result()["message"]["content"] == "ok"
        assert second.result()["message"]["content"] == "ok"


@pytest.mark.parametrize("budget_stage", ["review", "reason"])
def test_default_allowance_prevents_reasoning_only_stock_result(tools, budget_stage):
    """Replay the observed all-reasoning/empty-output failure at the real agent seam."""
    budgets = []

    def handler(request):
        body = json.loads(request.read())
        review = body["tools"][0]["function"]["name"] == "submit_review"
        if review == (budget_stage == "review"):
            budgets.append(body["max_tokens"])
            if body["max_tokens"] < 8192:
                return httpx.Response(
                    200,
                    json={
                        "id": "truncated",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": ""},
                                "finish_reason": "length",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 4096,
                            "total_tokens": 4196,
                            "completion_tokens_details": {"reasoning_tokens": 4096},
                        },
                    },
                )
        if review:
            claims = json.loads(body["messages"][1]["content"])["claims"]
            name, payload = (
                "submit_review",
                {
                    "verdicts": [
                        {
                            "index": c["index"],
                            "supported": True,
                            "reason": "Controlled completed review",
                        }
                        for c in claims
                    ]
                },
            )
        else:
            context = json.loads(body["messages"][1]["content"])
            growth = next(
                e for e in context["annual_overview"]["evidence"] if e.get("operation") == "growth"
            )
            name, payload = (
                "submit_answer",
                {
                    "claims": [
                        {
                            "kind": "analysis",
                            "statement": "年度营收增长支持经营改善，需观察其能否持续。",
                            "evidence_ids": [growth["id"]],
                        }
                    ],
                    "gaps": [],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "completed",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(payload)},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    settings = Settings(
        home=tools.corpus.home, api_key="test", model="test", min_model_interval=0, max_rounds=1
    )
    gateway = OpenAIGateway(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    answer = ResearchAgent(settings, gateway).run(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert any(c["kind"] == "analysis" for c in answer.answer), answer.trace
    assert budgets == [8192]


@pytest.mark.parametrize("failure", ["timeout", "connection", 502])
def test_transient_failure_is_retried(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("filingcopilot.agent.time.sleep", lambda seconds: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("temporary", request=request)
            if failure == "connection":
                raise httpx.ConnectError("temporary", request=request)
            return httpx.Response(failure, json={"error": {"message": "temporary"}})
        return response()

    assert gateway(tmp_path, handler).complete([], None)["message"]["content"] == "ok"
    assert len(attempts) == 2


def test_authentication_error_is_not_retried(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    with pytest.raises(Exception):
        gateway(tmp_path, handler).complete([], None)
    assert len(attempts) == 1


def test_retry_budget_is_finite(tmp_path, monkeypatch):
    monkeypatch.setattr("filingcopilot.agent.time.sleep", lambda seconds: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ReadTimeout("temporary", request=request)

    with pytest.raises(Exception) as caught:
        gateway(tmp_path, handler).complete([], None)
    assert len(attempts) == 3
    assert len(caught.value.gateway_retries) == 3


def test_rate_limit_obeys_retry_after_and_records_attempts(tmp_path, monkeypatch):
    now, waits, attempts = [0.0], [], []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("filingcopilot.agent.time.sleep", sleep)

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                429, headers={"retry-after": "40"}, json={"error": {"message": "quota"}}
            )
        return response()

    client = gateway(tmp_path, handler)
    client.clock = lambda: now[0]
    result = client.complete([], None)
    assert result["attempts"] == 2 and sum(waits) >= 40
    assert result["retries"][0]["status"] == 429


def test_excessive_retry_after_stops_without_retrying_early(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(
            429, headers={"retry-after": "300"}, json={"error": {"message": "quota"}}
        )

    with pytest.raises(Exception):
        gateway(tmp_path, handler).complete([], None)
    assert len(attempts) == 1


def test_rate_limit_slots_are_independent_between_models(tmp_path, monkeypatch):
    now, waits = [0.0], []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("filingcopilot.agent.time.sleep", sleep)
    client = gateway(tmp_path, lambda request: response())
    client.settings = Settings(
        home=tmp_path, model="test", review_model="judge", min_model_interval=10
    )
    client.clock = lambda: now[0]
    client.complete([], None)
    client.complete([], None, review=True)
    assert not waits
    client.complete([], None)
    assert waits == [10]


def test_concurrent_request_starts_keep_minimum_interval(tmp_path):
    starts, lock = [], threading.Lock()

    def handler(request):
        with lock:
            starts.append(time.monotonic())
        return response()

    client = gateway(tmp_path, handler)
    client.settings = Settings(home=tmp_path, model="test", min_model_interval=0.03)
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(client.complete, [], None) for _ in range(4)]
        for future in futures:
            future.result(timeout=3)
    assert all(b - a >= 0.025 for a, b in zip(starts, starts[1:]))


def test_delayed_sdk_preparation_cannot_bunch_actual_request_starts(tmp_path, monkeypatch):
    prepared, release = threading.Event(), threading.Event()
    starts, counter, lock = [], [0], threading.Lock()

    def handler(request):
        starts.append(time.monotonic())
        release.set()
        return response()

    client = gateway(tmp_path, handler)
    client.settings = Settings(home=tmp_path, model="test", min_model_interval=0.03)
    original_create = client.client.chat.completions.create

    def delayed_create(**kwargs):
        with lock:
            counter[0] += 1
            first = counter[0] == 1
        if first:
            prepared.set()
            assert release.wait(3)
        return original_create(**kwargs)

    monkeypatch.setattr(client.client.chat.completions, "create", delayed_create)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.complete, [], None)
        assert prepared.wait(2)
        second = pool.submit(client.complete, [], None)
        first.result(timeout=3)
        second.result(timeout=3)
    assert starts[1] - starts[0] >= 0.025
