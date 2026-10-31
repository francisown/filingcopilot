"""Representation quirks must not erase valid reviews or manufacture approvals."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import ResearchRequest
from filingcopilot.settings import Settings


@pytest.mark.parametrize("encoding", ["array", "envelope_tail", "retry_truncated", "always_truncated"])
@pytest.mark.parametrize("supported", [True, False])
def test_encoded_review_preserves_verdict_and_gap_through_agent(tools, encoding, supported):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            calls.append(review)
            if review:
                if encoding == "always_truncated" or (encoding == "retry_truncated" and calls.count(True) == 1):
                    return {"message": {"content": '{"verdicts":[{"index":0'}, "model": "reviewer"}
                verdicts = [{"index": 0, "supported": supported, "reason": "source verdict"}]
                gaps = [{"index": 0, "required_missing": False, "reason": "unrequested amount"}]
                payload = {"verdicts": json.dumps(verdicts), "gap_verdicts": json.dumps(gaps)}
                if encoding == "envelope_tail":
                    payload = {"verdicts": json.dumps(verdicts) + ', "gap_verdicts": ' + json.dumps(gaps) + '}'}
                name = "submit_review"
            else:
                name = "submit_answer"
                payload = {"claims": [{"kind": "narrative", "evidence_ids": ["p1"],
                                       "statement": "Supply disruptions could adversely affect operations."}],
                           "gaps": ["The exact loss amount is not disclosed."]}
            return {"message": {"tool_calls": [{"id": str(len(calls)), "type": "function",
                     "function": {"name": name, "arguments": json.dumps(payload)}}]},
                    "model": "reviewer" if review else "author"}

    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off",
                                    max_rounds=1, min_model_interval=0), Gateway()).run(
        ResearchRequest(question="What supply risks does Apple disclose?", ticker="AAPL",
                        language="en", as_of=tools.as_of), tools)
    assert calls == ([False, True, True] if encoding in {"retry_truncated", "always_truncated"} else [False, True])
    if encoding == "always_truncated":
        assert not answer.answer and answer.status == "abstained"
        assert sum(t["stage"] == "semantic_review_format_retry" for t in answer.trace) == 1
        assert any(t["stage"] == "semantic_review_error" for t in answer.trace)
        return
    assert not any(t["stage"] == "semantic_review_error" for t in answer.trace)
    assert bool(answer.answer) is supported
    if supported:
        assert answer.status == "answered"
    else:
        assert answer.status == "abstained"


@pytest.mark.parametrize("content", [
    '{"verdicts":"[{\\"index\\":0"}',
    '{"verdicts":[],"verdicts":[{"index":0,"supported":true,"reason":"overwritten"}]}',
    json.dumps({"verdicts": '[], "verdicts": []}'}),
    json.dumps({"verdicts": '[], "gap_verdicts": []}', "gap_verdicts": []}),
    json.dumps({"verdicts": json.dumps([{"index": 0, "supported": "true", "reason": "bad bool"}])}),
    json.dumps({"verdicts": json.dumps([{"index": "0", "supported": True, "reason": "bad index"}])}),
    json.dumps({"verdicts": json.dumps([{"index": 0, "supported": True, "reason": "extra", "other": 1}])}),
    json.dumps({"verdicts": '"[]"'}),
    json.dumps({"verdicts": '[' + ' ' * 60000 + ']'}),
], ids=["truncated", "duplicate", "duplicate_tail", "conflicting_gap", "string_bool",
        "string_index", "extra_field", "recursive_encoding", "oversize"])
def test_ambiguous_incomplete_or_coerced_reviews_are_rejected(content):
    from filingcopilot.verification import ReviewBatch, parse_review

    with pytest.raises(ValueError):
        ReviewBatch.model_validate(parse_review(content))
