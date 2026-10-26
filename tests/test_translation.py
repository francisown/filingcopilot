from __future__ import annotations

import json

import pytest

from filingcopilot.service import FilingCopilot


class FakeGateway:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def complete(self, messages, tools, review=False):
        self.calls.append((messages, tools, review))
        return {"message": {"role": "assistant", "content": self.content}}


def service_with(content: str) -> tuple[FilingCopilot, FakeGateway]:
    service = object.__new__(FilingCopilot)
    service.gateway = FakeGateway(content)
    return service, service.gateway


def test_translate_texts_preserves_order_and_uses_no_tools() -> None:
    service, gateway = service_with(json.dumps(["问题译文", "回答译文", "缺口译文"], ensure_ascii=False))
    assert service.translate_texts(["Question?", "Answer.", "Gap."]) == [
        "问题译文",
        "回答译文",
        "缺口译文",
    ]
    assert gateway.calls[0][1] is None
    assert gateway.calls[0][2] is False


def test_translate_texts_accepts_fenced_json_and_rejects_wrong_count() -> None:
    service, _ = service_with("```json\n[\"正确\"]\n```")
    assert service.translate_texts(["Question?"]) == ["正确"]

    service, _ = service_with('["only one"]')
    with pytest.raises(ValueError, match="译文数量"):
        service.translate_texts(["one", "two"])
