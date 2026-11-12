"""Compact review verdicts and a single review-driven repair.

The reviewer output contract is deliberately short, and a review rejection
must not fund a second full author+review cycle. Deterministic failures keep
their existing repair budget.
"""

import json

from filingcopilot.agent import ResearchAgent
from filingcopilot.execution import research_scope
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.review_protocol import review_tool
from filingcopilot.settings import Settings


def test_review_schema_uses_compact_lengths_and_keeps_required_fields():
    schema = review_tool([1], [0])
    parameters = schema["function"]["parameters"]
    properties = parameters["properties"]

    assert properties["claim_1_scope_check"]["maxLength"] == 320
    assert "at most 240 characters" in properties["claim_1_scope_check"]["description"]
    assert properties["claim_1_reason"]["maxLength"] == 240
    assert properties["claim_1_reason"]["description"]
    assert properties["gap_0_reason"]["maxLength"] == 240

    # Coverage-index review shares the claim scope_check bound.
    coverage = review_tool([1], [], coverage_index=1)["function"]["parameters"]["properties"]
    assert coverage["claim_1_scope_check"]["maxLength"] == 320
    assert "At most 240 characters" in coverage["claim_1_scope_check"]["description"]

    # The output-length change must not add, remove or reorder any field.
    assert parameters["required"] == list(properties)
    assert parameters["required"] == [
        "claim_1_scope_check",
        "claim_1_scope_supported",
        "claim_1_source_supported",
        "claim_1_inference_supported",
        "claim_1_document_absence",
        "claim_1_reason",
        "gap_0_required_missing",
        "gap_0_description_supported",
        "gap_0_search_query",
        "gap_0_reason",
    ]


class _Gateway:
    """Scripted fake gateway: author submits a narrative claim; reviewer votes."""

    def __init__(self, submission, verdicts):
        self.submission = submission
        self.verdicts = list(verdicts)
        self.author_calls = 0
        self.review_calls = 0

    def complete(self, messages, schemas, review=False):
        if not review:
            self.author_calls += 1
            return {
                "model": "author",
                "message": {
                    "tool_calls": [
                        {
                            "id": "submit",
                            "function": {
                                "name": "submit_answer",
                                "arguments": json.dumps(self.submission),
                            },
                        }
                    ]
                },
            }
        self.review_calls += 1
        supported = self.verdicts[min(self.review_calls - 1, len(self.verdicts) - 1)]
        context = json.loads(messages[-1]["content"])
        return {
            "model": "reviewer",
            "review_independence": "independent",
            "message": {
                "content": json.dumps(
                    {
                        "verdicts": [
                            {
                                "index": claim["index"],
                                "supported": supported,
                                "reason": "Claim does not match the cited passage.",
                            }
                            for claim in context["claims"]
                        ]
                    }
                )
            },
        }


def _submission():
    return {
        "claims": [
            {
                "kind": "narrative",
                "statement": "Supply disruptions could adversely affect operations.",
                "evidence_ids": ["p1"],
            }
        ],
        "gaps": [],
    }


def _request(as_of):
    return ResearchRequest(
        question="What does the company disclose about manufacturing partners?",
        ticker="AAPL",
        language="en",
        as_of=as_of,
    )


def test_two_review_rejections_allow_only_one_repair(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    gateway = _Gateway(_submission(), [False, False])
    answer = ResearchAgent(
        Settings(home=tools.corpus.home, max_rounds=8, min_model_interval=0), gateway
    ).run(_request(tools.as_of), tools)

    # One initial author turn plus exactly one review-driven repair.
    assert gateway.author_calls == 2
    assert gateway.review_calls == 2
    assert not any(item["kind"] == "narrative" for item in answer.answer)
    assert answer.status in {"partial", "abstained"}


def test_review_rejection_followed_by_repair_delivers_supported_claim(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    gateway = _Gateway(_submission(), [False, True])
    answer = ResearchAgent(
        Settings(home=tools.corpus.home, max_rounds=8, min_model_interval=0), gateway
    ).run(_request(tools.as_of), tools)

    assert gateway.author_calls == 2
    # The repaired draft is reviewed again rather than trusted.
    assert gateway.review_calls == 2
    assert [item["kind"] for item in answer.answer] == ["narrative"]


def test_deterministic_failure_keeps_the_existing_repair_budget(tools):
    class Gateway:
        def complete(self, messages, schemas, review=False):
            raise AssertionError("no review is needed for an unobserved citation")

    state = dict(
        submission=Submission(
            claims=[Claim(kind="narrative", statement="Unobserved claim.", evidence_ids=["missing"])]
        ),
        tools=tools,
        question="What does the company disclose about manufacturing partners?",
        focus_ticker="AAPL",
        rounds=1,
        repairs=0,
        messages=[],
        verified=[],
        errors=[],
        trace=[],
        tool_count=0,
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0),
        action="",
    )
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)

    assert result["action"] == "reason"
    assert result["review_rejected"] is False
    assert result["review_repairs"] == 0
    assert result["repairs"] == 1


def _review_payload(context, *, supported):
    payload = {}
    for index in context["required_indexes"]:
        payload[f"claim_{index}_scope_check"] = "Scope matches the cited passage."
        payload[f"claim_{index}_scope_supported"] = supported
        payload[f"claim_{index}_source_supported"] = supported
        payload[f"claim_{index}_inference_supported"] = supported
        payload[f"claim_{index}_document_absence"] = False
        payload[f"claim_{index}_reason"] = "Review verdict for this test."
    return json.dumps(payload)


def _review_state(tools, **overrides):
    state = dict(
        submission=Submission(claims=[Claim(
            kind="narrative", evidence_ids=["p1"],
            statement="Supply disruptions could adversely affect operations.",
        )]),
        tools=tools,
        question="Why did Apple's revenue change?",
        focus_ticker="AAPL",
        rounds=1,
        repairs=1,
        review_repairs=1,
        messages=[],
        verified=[],
        errors=[],
        trace=[],
        tool_count=0,
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0),
        action="",
    )
    state.update(overrides)
    return state


def test_exhausted_review_repair_budget_disables_retrieval_and_second_repair(tools, monkeypatch):
    """A review-rejected state with one spent review repair must stop, not search."""
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            context = json.loads(messages[-1]["content"])
            return {
                "model": "judge",
                "review_independence": "independent",
                "message": {"content": _review_payload(context, supported=False)},
            }

    agent = ResearchAgent(Settings(home=tools.corpus.home), Gateway())
    retrieval_calls = []
    monkeypatch.setattr(
        agent, "_retrieve_gap_evidence", lambda *args, **kwargs: retrieval_calls.append(args)
    )
    state = _review_state(tools)
    result = agent.verify(state)

    assert result["review_rejected"] is True
    assert retrieval_calls == [], "A spent review repair must not fund gap retrieval"
    assert result["action"] == "end"
    assert result["messages"] == [], "No second review-driven repair may be queued"
    assert result["errors"], "Coverage findings remain visible without a repair turn"


def test_reason_reserve_covers_the_configured_review_timeout(tools):
    """Research must leave the final review at least one full request timeout."""
    recorded = []

    class Control:
        cancelled = False
        expired = False
        cost_exceeded = False
        time_budget = 900.0
        remaining = 100.0

    agent = ResearchAgent(
        Settings(
            home=tools.corpus.home,
            review_timeout=300,
            review_reserve_seconds=30,
        ),
        object(),
    )
    state = dict(question="Why did revenue change?", tools=tools, trace=[], rounds=0, errors=[])
    with research_scope(Control()):
        result = agent.reason(state)
        recorded.append(result["trace"][-1])

    assert result["action"] == "end"
    assert recorded[0]["stage"] == "review_reserve"
    assert recorded[0]["reserve_seconds"] >= 300


def test_failed_semantic_review_records_one_timed_failure_entry(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            raise TimeoutError("Reviewer unavailable after bounded request retries")

    state = _review_state(tools)
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)

    failures = [step for step in result["trace"] if step.get("stage") == "semantic_review_error"]
    assert len(failures) == 1, "A single detailed failure receipt avoids duplicate noise"
    assert failures[0]["duration_ms"] >= 0
    assert "retry_wait_ms" in failures[0]
    assert failures[0]["error_type"] == "TimeoutError"
    assert failures[0]["verdicts"][0]["index"] == 0
    assert result["review_failed"] is True
