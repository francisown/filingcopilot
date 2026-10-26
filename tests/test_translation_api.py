from __future__ import annotations

import json

from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


class FakeGateway:
    def complete(self, messages, tools, review=False):
        return {"message": {"role": "assistant", "content": json.dumps(["中文问题", "中文回答"])}}


def test_translation_endpoint_returns_ordered_translations(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, model="", review_model="", api_base="", api_key=""))
    service.gateway = FakeGateway()
    client = TestClient(create_app(service))
    response = client.post(
        "/api/translate",
        json={"texts": ["Question?", "Answer."], "target": "zh"},
    )
    assert response.status_code == 200
    assert response.json() == {"translations": ["中文问题", "中文回答"], "target": "zh"}
