import asyncio

from fastapi.testclient import TestClient

from filingcopilot.auth import SharedTokenVerifier, authorization_token
from filingcopilot.cli import bind_allowed, is_loopback_host
from filingcopilot.mcp_server import create_mcp
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def test_only_loopback_hosts_are_accepted() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")
    assert not is_loopback_host("example.com")


def test_non_loopback_requires_explicit_container_or_proxy_override() -> None:
    assert not bind_allowed("0.0.0.0")
    assert bind_allowed("0.0.0.0", allow_non_loopback=True)
    assert bind_allowed("127.0.0.1")


def test_shared_http_auth_accepts_bearer_and_basic_password_only() -> None:
    assert authorization_token("Bearer secret") == "secret"
    assert authorization_token("Basic dXNlcjpzZWNyZXQ=") == "secret"
    assert authorization_token("Basic dXNlcjo=") == ""
    assert authorization_token("Bearer") is None
    assert authorization_token("Digest secret") is None


def test_mcp_shared_token_verifier_rejects_wrong_secret() -> None:
    verifier = SharedTokenVerifier("secret")
    assert asyncio.run(verifier.verify_token("wrong")) is None
    token = asyncio.run(verifier.verify_token("secret"))
    assert token is not None
    assert token.client_id == "filingcopilot-local"


def test_streamable_mcp_http_requires_configured_bearer_token(tmp_path) -> None:
    mcp = create_mcp(
        FilingCopilot(Settings(home=tmp_path, embedding_mode="off", auth_token="secret")),
        port=8998,
    )
    with TestClient(mcp.streamable_http_app()) as client:
        assert client.get("/mcp").status_code == 401
        assert client.get("/mcp", headers={"Authorization": "Bearer secret"}).status_code != 401
