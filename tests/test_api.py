import json
import logging

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


@pytest.fixture
def service(tmp_path):
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))


def test_response_echoes_safe_request_id(service):
    with TestClient(create_app(service)) as client:
        response = client.get(
            "/api/health", headers={"X-Request-ID": "demo-request.1:abc_2"}
        )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "demo-request.1:abc_2"


def test_unsafe_request_id_is_replaced_with_generated_hex(service):
    candidate = "not allowed! " + "x" * 200
    with TestClient(create_app(service)) as client:
        response = client.get("/api/health", headers={"X-Request-ID": candidate})
    request_id = response.headers["x-request-id"]
    assert request_id != candidate
    assert len(request_id) == 32
    assert all(character in "0123456789abcdef" for character in request_id)


def test_authentication_rejection_carries_request_id(tmp_path):
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", auth_token="secret")
    )
    with TestClient(create_app(service)) as client:
        response = client.get(
            "/api/health", headers={"X-Request-ID": "auth-check"}
        )
    assert response.status_code == 401
    assert response.headers["x-request-id"] == "auth-check"


def test_access_log_emits_one_structured_line(tmp_path, caplog):
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", access_log=True)
    )
    with caplog.at_level(logging.INFO, logger="filingcopilot.access"):
        with TestClient(create_app(service)) as client:
            response = client.get(
                "/api/health?ticker=SECRET",
                headers={"X-Request-ID": "logged-request"},
            )
    assert response.status_code == 200
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "filingcopilot.access"
    ]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "http_request"
    assert payload["request_id"] == "logged-request"
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/health"
    assert payload["status"] == 200
    assert isinstance(payload["duration_ms"], float)
    assert "SECRET" not in lines[0]
    assert set(payload) == {
        "event",
        "request_id",
        "method",
        "path",
        "status",
        "duration_ms",
    }


def test_access_log_is_silent_by_default(service, caplog):
    with caplog.at_level(logging.INFO, logger="filingcopilot.access"):
        with TestClient(create_app(service)) as client:
            response = client.get("/api/health")
    assert response.status_code == 200
    assert not [
        record
        for record in caplog.records
        if record.name == "filingcopilot.access"
    ]
