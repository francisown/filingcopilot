"""Recall keeps observed evidence addressable without repeating acquisition."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.agent_execution import _model_tool_content, _read_recall
from filingcopilot.recall import RecallIndex
from filingcopilot.settings import Settings

LONG_TEXT = "Supply disruptions could adversely affect operations. " * 200


def passage(ref="p1", text="The company relies on manufacturing partners."):
    return {
        "id": ref,
        "filing_id": "AAPL:2025",
        "ticker": "AAPL",
        "section": "Risk factors",
        "text": text,
        "page": 9,
    }


def execute_state(tools, calls, *, recall=None, tool_count=2):
    return dict(
        question="What was revenue?",
        tools=tools,
        trace=[],
        rounds=1,
        tool_count=tool_count,
        seen=set(),
        usage={},
        errors=[],
        recall=recall if recall is not None else RecallIndex(),
        messages=[{"role": "assistant", "tool_calls": calls}],
    )


def test_index_keeps_full_payload_and_exposes_a_short_pointer():
    recall = RecallIndex(snippet_chars=40)
    recall.index({"hits": [{"passage": passage(text=LONG_TEXT)}]}, pin=True)

    entry = recall.entry("p1")
    assert entry["pinned"] is True
    assert entry["kind"] == "passage"
    assert entry["snippet"].endswith("…")
    assert len(entry["snippet"]) <= 40
    assert recall.read("p1")["text"] == LONG_TEXT
    assert recall.pointers(["p1", "missing"]) == [
        {
            "id": "p1",
            "kind": "passage",
            "snippet": entry["snippet"],
            "ticker": "AAPL",
            "section": "Risk factors",
            "page": 9,
            "filing_id": "AAPL:2025",
        }
    ]


def test_read_returns_a_copy_so_callers_cannot_mutate_the_index():
    recall = RecallIndex()
    recall.index(passage())
    first = recall.read("p1")
    first["text"] = "changed"
    assert recall.read("p1")["text"] == "The company relies on manufacturing partners."


def test_index_keeps_the_richest_projection_for_the_same_id():
    recall = RecallIndex()
    recall.index(passage(text="The same paragraph returned in full."))
    recall.index(
        {
            "hits": [
                {
                    "passage": {
                        "id": "p1",
                        "filing_id": "AAPL:2025",
                        "ticker": "AAPL",
                        "section": "Risk factors",
                        "page": 9,
                        "reused": True,
                    }
                }
            ]
        }
    )
    assert recall.read("p1")["text"] == "The same paragraph returned in full."


def test_index_ignores_values_without_a_stable_evidence_id():
    recall = RecallIndex()
    recall.index({"hits": [{"score": 0.5}], "note": "no id here"})
    assert recall.entries == {}


def tool_message(index, text="x" * 8000):
    return {
        "role": "tool",
        "tool_call_id": "call-" + str(index),
        "content": json.dumps({"found": True, "evidence": [{"id": "p" + str(index), "text": text}]}),
    }


def test_compaction_leaves_short_context_untouched():
    recall = RecallIndex()
    messages = [tool_message(index) for index in range(3)]
    assert recall.compact(messages) is messages

    small = [tool_message(index, text="x" * 100) for index in range(10)]
    assert recall.compact(small) is small


def test_compaction_replaces_only_old_large_tool_output_with_pointers():
    recall = RecallIndex()
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}]
    messages += [tool_message(index) for index in range(8)]
    for message in messages:
        if message["role"] == "tool":
            recall.index(json.loads(message["content"]))
    originals = [message["content"] for message in messages]

    compacted = recall.compact(messages)

    assert messages[2]["content"] == originals[2], "the stored trace copy must stay complete"
    assert compacted[2]["content"] != originals[2]
    assert compacted[3]["content"] != originals[3]
    first = json.loads(compacted[2]["content"])
    assert first["recall"]["ids"] == ["p0"]
    assert first["recall"]["preview"][0]["id"] == "p0"
    assert first["summary"] == {"found": True}
    assert "read_recall" in first["note"]
    # The newest six tool results are still complete, as are non-tool messages.
    assert all(
        json.loads(compacted[index]["content"])["evidence"][0]["text"] == "x" * 8000
        for index in range(4, 10)
    )
    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]

    again = recall.compact(compacted)
    assert again is compacted, "compaction is idempotent and stops once context is small"


def test_read_recall_prefers_the_index_and_falls_back_to_the_ledger(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    evidence = tools.ledger["rev25"]
    recall = RecallIndex()
    recall.index(evidence)

    result = _read_recall({"recall": recall, "tools": tools}, {"id": "rev25"})
    assert result == {"evidence": evidence}
    assert _read_recall({"tools": tools}, {"id": "rev25"}) == {"evidence": evidence}

    with pytest.raises(ValueError, match="observed"):
        _read_recall({"recall": recall, "tools": tools}, {"id": "not-observed"})
    with pytest.raises(ValueError, match="requires"):
        _read_recall({"recall": recall, "tools": tools}, {"id": "  "})


def test_model_tool_content_indexes_the_projected_view(tools):
    tools.search_passages("manufacturing supply", "AAPL")
    evidence = tools.ledger["p1"]
    state = {"recall": RecallIndex(), "tools": tools, "context_passage_ids": set()}

    content = _model_tool_content(state, {"hits": [{"passage": evidence, "score": 0.9}]})

    assert state["recall"].read("p1")["text"] == evidence["text"]
    assert json.loads(content)["hits"][0]["passage"]["text"] == evidence["text"]


def test_read_recall_after_the_budget_is_spent_and_keeps_full_text(tools):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    evidence = tools.ledger["rev25"]
    recall = RecallIndex()
    recall.index(evidence)
    arguments = json.dumps({"id": "rev25"})
    calls = [
        {
            "id": "call-" + str(index),
            "function": {"name": "read_recall", "arguments": arguments},
        }
        for index in range(2)
    ]
    state = execute_state(tools, calls, recall=recall)

    result = ResearchAgent(
        Settings(home=tools.corpus.home, max_tool_calls=2), object()
    ).execute(state)

    assert result["tool_count"] == 2
    assert result["usage"].get("tool_calls", 0) == 0
    assert result["action"] == "reason"
    tool_messages = [message for message in result["messages"] if message["role"] == "tool"]
    assert len(tool_messages) == 2
    for message in tool_messages:
        payload = json.loads(message["content"])
        assert payload["evidence"] == evidence
        assert "reused" not in payload
