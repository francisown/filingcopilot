"""Gateway resilience: tool_choice capability fallback and budget-aware failover.

Some providers (thinking-mode models) reject tool_choice="required"; a rejected
choice must fall back to "auto" instead of failing the run, and the learned
capability must be reused.  A model that keeps failing must fail over to the
other configured model, but only while the research budget can absorb it.
"""

import json

import httpx
import pytest

from filingcopilot.agent import OpenAIGateway
from filingcopilot.execution import ResearchControl, research_scope
from filingcopilot.settings import Settings

# Integration scope; included in full runs.
pytestmark = pytest.mark.slow

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": "Submit the answer.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def response(model: str = "test"):
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def gateway(tmp_path, handler, **overrides):
    settings = {
        "home": tmp_path,
        "api_key": "test",
        "model": "test",
        "min_model_interval": 0,
        "retry_base_delay": 0,
    }
    settings.update(overrides)
    return OpenAIGateway(
        Settings(**settings), http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_rejected_required_tool_choice_retries_with_auto(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.read())
        seen.append(body.get("tool_choice"))
        if body.get("tool_choice") == "required":
            return httpx.Response(
                400, json={"error": {"message": "Thinking mode does not support this tool_choice"}}
            )
        return response()

    result = gateway(tmp_path, handler).complete([], TOOLS)
    assert result["message"]["content"] == "ok"
    assert seen == ["required", "auto"]


def test_learned_tool_choice_capability_is_reused(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.read())
        seen.append(body.get("tool_choice"))
        if body.get("tool_choice") == "required":
            return httpx.Response(
                400, json={"error": {"message": "Thinking mode does not support this tool_choice"}}
            )
        return response()

    client = gateway(tmp_path, handler)
    client.complete([], TOOLS)
    client.complete([], TOOLS)
    assert seen == ["required", "auto", "auto"]


def test_unrelated_bad_request_is_not_retried_as_a_fallback(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(400, json={"error": {"message": "unknown model"}})

    with pytest.raises(Exception):
        gateway(tmp_path, handler).complete([], TOOLS)
    assert len(attempts) == 1


def test_primary_model_failure_fails_over_to_the_other_model(tmp_path):
    models = []

    def handler(request):
        body = json.loads(request.read())
        models.append(body["model"])
        if body["model"] == "test":
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return response(model="judge")

    result = gateway(tmp_path, handler, review_model="judge").complete([], None)
    assert result["model"] == "judge"
    assert models[:3] == ["test", "test", "test"]
    assert models[-1] == "judge"


def test_failover_is_skipped_when_budget_cannot_absorb_it(tmp_path):
    models = []

    def handler(request):
        body = json.loads(request.read())
        models.append(body["model"])
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = gateway(tmp_path, handler, review_model="judge")
    control = ResearchControl()
    control.start_budget(30)
    with research_scope(control), pytest.raises(Exception):
        client.complete([], None)
    assert models == ["test", "test", "test"]


def test_review_prefers_a_model_that_did_not_produce_the_answer(tmp_path):
    review_tools = [
        {
            "type": "function",
            "function": {
                "name": "submit_review",
                "description": "Return verdicts.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    def handler(request):
        body = json.loads(request.read())
        name = body["tools"][0]["function"]["name"]
        # The research model is down; the reviewer answers normally.
        if body["model"] == "test" and name != "submit_review":
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return response(model=body["model"])

    client = gateway(tmp_path, handler, review_model="judge")
    control = ResearchControl()
    with research_scope(control):
        research = client.complete([], TOOLS)
        assert research["model"] == "judge"  # failover happened
        assert control.research_models == {"judge"}
        review = client.complete([], review_tools, review=True)
    assert review["model"] == "test"  # independent reviewer preferred
    assert "review_independence" not in review


def test_review_discloses_when_no_independent_model_remains(tmp_path):
    def handler(request):
        body = json.loads(request.read())
        return response(model=body["model"])

    client = gateway(tmp_path, handler, review_model="judge")
    # Both configured models produced research this run.
    control = ResearchControl()
    control.record_author("test")
    control.record_author("judge")
    with research_scope(control):
        review = client.complete([], TOOLS, review=True)
    assert review["model"] == "judge"
    assert review["review_independence"] == "not_independent"


def test_malformed_extra_headers_do_not_take_the_gateway_down(tmp_path, monkeypatch):
    monkeypatch.setenv("FC_EXTRA_HEADERS", "{not valid json")
    monkeypatch.setenv("FC_SESSION_ID", "session-1")
    result = gateway(tmp_path, lambda request: response()).complete([], None)
    assert result["message"]["content"] == "ok"


def test_failover_receipt_includes_failed_primary_time_and_attempts(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("filingcopilot.agent_gateway.time.perf_counter", lambda: now[0])

    def handler(request):
        model = json.loads(request.read())["model"]
        now[0] += 5 if model == "test" else 3
        if model == "test":
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return response(model)

    result = gateway(tmp_path, handler, review_model="judge", model_attempts=2).complete([], None)
    assert result["model"] == "judge"
    assert result["duration_ms"] == 13000
    assert result["attempts"] == 3
    assert [r["status"] for r in result["retries"]] == [503, 503]
    assert all(r["model"] == "test" for r in result["retries"])


@pytest.mark.parametrize("attempts,prior_failures", [(1, 0), (3, 0), (3, 2)])
def test_provider_cooldown_is_not_bypassed_by_model_failover(tmp_path, attempts, prior_failures):
    models = []

    def handler(request):
        models.append(json.loads(request.read())["model"])
        if len(models) <= prior_failures:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(
            429, headers={"retry-after": "120"}, json={"error": {"message": "provider cooldown"}}
        )

    with pytest.raises(Exception):
        gateway(
            tmp_path, handler, review_model="judge", retry_max_delay=30, model_attempts=attempts
        ).complete([], None)
    assert models == ["test"] * (prior_failures + 1)
