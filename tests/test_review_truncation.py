"""A truncated review must retry with a larger token ceiling, not drop claims."""

from __future__ import annotations

import json
from pathlib import Path

from filingcopilot.agent import ResearchAgent
from filingcopilot.review_protocol import review_tool
from filingcopilot.settings import Settings


def _response(content: str, finish_reason: str) -> dict:
    return {
        "message": {"role": "assistant", "content": content},
        "usage": {},
        "duration_ms": 1,
        "model": "judge",
        "attempts": 1,
        "retries": [],
        "finish_reason": finish_reason,
    }


def test_truncated_review_escalates_token_ceiling_once() -> None:
    calls = []
    valid = json.dumps(
        {
            "claim_0_scope_check": "same entity and period",
            "claim_0_scope_supported": True,
            "claim_0_source_supported": True,
            "claim_0_inference_supported": True,
            "claim_0_document_absence": False,
            "claim_0_reason": "supported",
        }
    )

    class Gateway:
        def complete(self, messages, tools, review=False, max_tokens=None):
            assert review
            calls.append(max_tokens)
            if len(calls) == 1:
                return _response("{}", "length")
            return _response(valid, "stop")

    settings = Settings(home=Path("unused-test-only"), review_max_tokens=8192)
    agent = ResearchAgent(settings, Gateway())
    state = {
        "trace": [],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "model_calls": 0,
            "review_calls": 0,
        },
    }
    parsed, _, _ = agent._request_semantic_review(
        state,
        [{"role": "user", "content": "review this claim"}],
        review_tool([0], []),
        [0],
        [],
    )

    assert calls == [None, 16384]
    assert parsed["verdicts"][0]["supported"] is True
    assert state["usage"]["review_calls"] == 2
    assert any(item.get("stage") == "semantic_review_truncated" for item in state["trace"])
