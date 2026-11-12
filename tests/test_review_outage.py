"""Reviewer outages must not become author rewrites or self-review loops."""
import json

import httpx
import pytest
from openai import APIStatusError

from filingcopilot.agent import ResearchAgent
from filingcopilot.agent_gateway import OpenAIGateway
from filingcopilot.execution import ResearchControl, research_scope
from filingcopilot.models import Claim, ResearchRequest, Submission
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
    assert models == ["reviewer", "reviewer"]
    assert "author" not in models


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


def test_failed_narrative_review_is_attempted_at_most_twice(tools):
    """One bounded verifier retry preserves answers without multiplying layers."""
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            calls.append("review")
            raise TimeoutError("Reviewer unavailable after bounded request retries")

    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    state = dict(
        submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                  statement="Supply disruptions could adversely affect operations.")]),
        tools=tools, question="What supply risks does Apple disclose?", focus_ticker="AAPL",
        rounds=1, repairs=0, messages=[], verified=[], errors=[], trace=[], tool_count=0,
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="",
    )
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    # The fake gateway has no transport retry; the callback withholds after one
    # attempt. The gateway's bounded retry is covered in test_gateway_resilience.
    assert calls == ["review"]
    assert result["review_failed"]
    assert not result["verified"]


def test_failed_source_review_skips_answer_coverage_review(tools):
    """A failed source review ends the pass; no second coverage call is made."""
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            calls.append("review")
            raise TimeoutError("Reviewer unavailable after bounded request retries")

    fact = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    state = dict(
        submission=Submission(claims=[
            Claim(kind="fact", evidence_ids=[fact["id"]], statement=""),
            Claim(kind="narrative", evidence_ids=["p1"],
                  statement="Supply disruptions could adversely affect operations."),
        ]),
        tools=tools, question="Why did Apple's FY2025 revenue change?", focus_ticker="AAPL",
        rounds=1, repairs=0, messages=[], verified=[], errors=[], trace=[], tool_count=0,
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="",
    )
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    assert len(calls) == 1, "Source review failed once; coverage review must not run"
    assert result["review_failed"]
    assert [c["kind"] for c in result["verified"]] == ["fact"]
    assert any("coverage remains unconfirmed" in error for error in result["errors"])
