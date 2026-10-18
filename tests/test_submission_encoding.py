"""Provider string-encoded arrays must not cause endless submit retries."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import ResearchRequest
from filingcopilot.settings import Settings
from filingcopilot.submission_protocol import parse_submission


@pytest.mark.parametrize("encoding", ["array", "envelope_tail"])
def test_encoded_submission_reaches_ordinary_review_once(tools, encoding):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    claims = [{"kind": "narrative", "evidence_ids": ["p1"],
               "statement": "Supply disruptions can adversely affect operations."}]
    arguments = {"claims": json.dumps(claims), "gaps": "[]"}
    if encoding == "envelope_tail":
        arguments = {"claims": json.dumps(claims) + ', "gaps": []}'}
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            calls.append(review)
            if review:
                batch = json.loads(messages[1]["content"])["claims"]
                return {"message": {"content": json.dumps({"verdicts": [
                    {"index": c["index"], "supported": True, "reason": "fixture source support"}
                    for c in batch]})}, "model": "reviewer"}
            return {"message": {"role": "assistant", "tool_calls": [{
                "id": "submit", "type": "function", "function": {
                    "name": "submit_answer", "arguments": json.dumps(arguments)}}]}, "model": "author"}

    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off", min_model_interval=0,
                                    max_rounds=3), Gateway()).run(
        ResearchRequest(question="What risks does Apple disclose?", ticker="AAPL",
                        as_of=tools.as_of, language="en"), tools,
    )
    assert answer.status == "answered", answer.gaps
    assert calls == [False, True]
    assert answer.answer[0]["text"] == claims[0]["statement"]


@pytest.mark.parametrize("arguments", [
    {"claims": '[{"kind":"narrative","statement":"unfinished'},
    {"claims": '[], "gaps": [], "unrequested": true}'},
    {"claims": '[], "claims": [], "gaps": []}'},
    {"claims": '[], "gaps": []}', "gaps": ["conflicting outer gap"]},
    {"claims": json.dumps([{"kind": "fact", "evidence_ids": ["invented"], "unknown": "field"}])},
    {"claims": '"[]"'},
    {"claims": "[" + " " * 60000 + "]"},
])
def test_malformed_or_ambiguous_representation_is_not_guessed(arguments):
    with pytest.raises(ValueError):
        parse_submission(arguments)


def test_decoding_cannot_make_an_unobserved_citation_valid(tools):
    from filingcopilot.verification import verify_submission
    submission = parse_submission({"claims": json.dumps([
        {"kind": "fact", "evidence_ids": ["invented"], "statement": ""}]), "gaps": "[]"})
    accepted, errors = verify_submission(submission, tools, "Apple FY2025 revenue", focus_ticker="AAPL")
    assert not accepted and any("not observed" in error for error in errors)
