"""Use JSON output for review data, avoiding provider tool-argument re-encoding."""
import json

import httpx
import pytest

from filingcopilot.agent_gateway import OpenAIGateway
from filingcopilot.review_protocol import review_tool
from filingcopilot.settings import Settings


@pytest.mark.parametrize("supported_mode", ["json_schema", "json_object", None])
def test_review_json_transport_and_bounded_capability_fallback(tmp_path, supported_mode):
    requests = []

    def handler(request):
        body = json.loads(request.read())
        requests.append(body)
        assert "tools" not in body and "tool_choice" not in body
        assert "claim_3_source_supported" in body["messages"][-1]["content"]
        mode = body.get("response_format", {}).get("type")
        if mode != supported_mode:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        if mode == "json_schema":
            assert body["response_format"]["json_schema"]["strict"] is True
            assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        return httpx.Response(200, json={"id": "review", "object": "chat.completion", "created": 0,
            "model": "reviewer", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"claim_3_source_supported":true,"claim_3_inference_supported":false,"claim_3_document_absence":false,"claim_3_reason":"Unsupported inference"}'}}]})

    gateway = OpenAIGateway(Settings(home=tmp_path, api_key="test", model="author", review_model="reviewer",
                                   model_attempts=1, min_model_interval=0),
                            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    messages = [{"role": "user", "content": "Review this claim."}]
    for _ in range(2):
        result = gateway.complete(messages, [review_tool([3], [])], review=True)
        assert json.loads(result["message"]["content"])["claim_3_inference_supported"] is False
    assert len(requests) == {"json_schema": 2, "json_object": 3, None: 4}[supported_mode]
    assert messages == [{"role": "user", "content": "Review this claim."}], "Do not mutate a cached prompt"


def test_review_request_has_its_own_timeout_and_keeps_the_global_deadline(tmp_path):
    from filingcopilot.execution import ResearchControl, research_scope

    observed = []

    def handler(request):
        observed.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"id": "reply", "object": "chat.completion", "created": 0,
            "model": json.loads(request.read())["model"], "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "{}"}}]})

    gateway = OpenAIGateway(Settings(home=tmp_path, api_key="test", model="author", review_model="reviewer",
        model_timeout=17, review_timeout=95, min_model_interval=0),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    gateway.complete([], None)
    gateway.complete([], None, review=True)
    control = ResearchControl()
    control.start_budget(12)
    with research_scope(control):
        gateway.complete([], None, review=True)
    assert observed[:2] == [17, 95]
    assert 0 < observed[2] <= 12
