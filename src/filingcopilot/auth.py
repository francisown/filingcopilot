"""Small, shared authentication primitives for local HTTP surfaces."""

from __future__ import annotations

import base64
import binascii
import hmac

from mcp.server.auth.provider import AccessToken


def authorization_token(value: str | None) -> str | None:
    """Extract the configured shared secret from Bearer or Basic auth."""
    if not value:
        return None
    scheme, _, encoded = value.partition(" ")
    if scheme.casefold() == "bearer":
        return encoded.strip() or None
    if scheme.casefold() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    _, separator, password = decoded.partition(":")
    return password if separator else None


class SharedTokenVerifier:
    """Verify one local shared secret for MCP streamable HTTP.

    This intentionally is not a user, role, or tenant system.  MCP clients send
    the secret as a Bearer token; deployments needing identity or rotation should
    put an authenticated proxy in front of the server.
    """

    def __init__(self, expected: str):
        if not expected:
            raise ValueError("A non-empty shared token is required")
        self.expected = expected

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.expected):
            return None
        return AccessToken(token=token, client_id="filingcopilot-local", scopes=[])
