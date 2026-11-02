"""Reviewer outages must not become author rewrites or self-review loops."""
import json

import httpx
import pytest
from openai import APIStatusError

from filingcopilot.agent import ResearchAgent
from filingcopilot.agent_gateway import OpenAIGateway
from filingcopilot.execution import ResearchControl, research_scope
from filingcopilot.models import ResearchRequest
from filingcopilot.settings import Settings


def test_unavailable_reviewer_never_falls_back_to_the_author(tmp_path):
    models = []

    def handler(request):
        model = json.loads(request.read())["model"]
        models.append(model)
        if model == "reviewer":
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, json={"id": "self-review", "object": "chat.completion",
            "created": 0, "model": model, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "{}"}}]})

    gateway = OpenAIGateway(Settings(home=tmp_path, api_key="test", model="author",
        review_model="reviewer", model_attempts=1, min_model_interval=0),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    control = ResearchControl()
    control.record_author("author")
    with research_scope(control), pytest.raises(APIStatusError):
        gateway.complete([], None, review=True)
    assert models == ["reviewer"]


def test_review_outage_is_not_sent_to_the_author_as_repair_feedback(tools):
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            calls.append(review)
            if review:
                raise TimeoutError("Reviewer unavailable after bounded request retries")
            return {"model": "author", "message": {"tool_calls": [{"id": "submit",
                "function": {"name": "submit_answer", "arguments": json.dumps({"claims": [
                    {"kind": "narrative", "statement": "Supply disruptions could adversely affect operations.",
                     "evidence_ids": ["p1"]}], "gaps": []})}}]}}

    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    answer = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=8, min_model_interval=0), Gateway()).run(
        ResearchRequest(question="What supply risks does Apple disclose?", ticker="AAPL",
                        language="en", as_of=tools.as_of), tools)
    assert calls == [False, True]
    assert answer.status in {"partial", "abstained"}
    assert not any(a["kind"] == "narrative" for a in answer.answer)
