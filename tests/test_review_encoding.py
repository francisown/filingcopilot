"""Representation quirks must not erase valid reviews or manufacture approvals."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.settings import Settings


def test_review_receives_source_section_scope(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["p1"]["section"] = "Regional Services"
    captured = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            captured.append(json.loads(messages[1]["content"]))
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": 0, "supported": True, "reason": "Matches this segment's risk disclosure"}]})}}

    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway())
    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
        statement="Supply disruptions could adversely affect operations.")]), tools=tools,
        question="What supply risks are disclosed?", focus_ticker="AAPL", rounds=1, repairs=2,
        messages=[], verified=[], errors=[], trace=[],
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    agent.verify(state)
    assert captured[0]["evidence"]["p1"].get("section") == "Regional Services"


@pytest.mark.parametrize("encoding", ["array", "envelope_tail", "retry_truncated", "always_truncated", "flat"])
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
                context = json.loads(messages[1]["content"])
                verdicts = [{"index": c["index"], "supported": supported, "reason": "source verdict"}
                            for c in context["claims"]]
                if all(c["kind"] == "answer_coverage" for c in context["claims"]):
                    return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": verdicts})}}
                gaps = [{"index": 0, "required_missing": False, "reason": "unrequested amount"}]
                payload = {"verdicts": json.dumps(verdicts), "gap_verdicts": json.dumps(gaps)}
                if encoding == "flat":
                    properties = schemas[0]["function"]["parameters"]["properties"]
                    assert all(p["type"] in {"boolean", "string"} for p in properties.values())
                    payload = {"gap_0_required_missing": False, "gap_0_reason": "unrequested amount"}
                    for c in context["claims"]:
                        index = c["index"]
                        payload.update({f"claim_{index}_source_supported": True,
                                        f"claim_{index}_inference_supported": supported,
                                        f"claim_{index}_document_absence": False,
                                        f"claim_{index}_scope_supported": True,
                                        f"claim_{index}_scope_check": "Scopes match",
                                        f"claim_{index}_reason": "source verdict"})
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
    expected_calls = [False, True, True] if encoding in {"retry_truncated", "always_truncated"} else [False, True]
    if supported and encoding != "always_truncated":
        expected_calls.append(True)  # Final answer coverage is isolated from source review.
    assert calls == expected_calls
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


@pytest.mark.parametrize("document_absence", [True, False])
def test_review_scope_classification_cannot_be_overridden_by_approval(document_absence):
    from filingcopilot.review_protocol import decode_review

    payload = {"claim_7_source_supported": True, "claim_7_inference_supported": True,
               "claim_7_scope_supported": True, "claim_7_scope_check": "Source scope is otherwise consistent",
               "claim_7_document_absence": document_absence, "claim_7_reason": "Binary approvals may be inconsistent"}
    result, _ = decode_review(payload, [7], [])
    assert result["verdicts"][0]["supported"] is not document_absence


def test_review_rejects_a_scope_expansion_even_when_other_verdicts_approve():
    from filingcopilot.review_protocol import decode_review

    payload = {"claim_2_source_supported": True, "claim_2_inference_supported": True,
               "claim_2_document_absence": False, "claim_2_scope_supported": False,
               "claim_2_scope_check": "The source limits the investment to one country; the claim adds another.",
               "claim_2_reason": "The broad summary otherwise matches."}
    result, _ = decode_review(payload, [2], [])
    assert result["verdicts"][0]["supported"] is False
    assert "one country" in result["verdicts"][0]["reason"]
