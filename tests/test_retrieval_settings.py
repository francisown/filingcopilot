"""Retrieval config switches must be explicit and environment-driven."""

from __future__ import annotations

from filingcopilot.settings import Settings


def test_retrieval_multi_query_defaults_on(monkeypatch):
    monkeypatch.setattr("filingcopilot.settings.load_dotenv", lambda **_kwargs: None)
    monkeypatch.delenv("FC_RETRIEVAL_MULTI_QUERY", raising=False)
    assert Settings.from_env().retrieval_multi_query is True


def test_retrieval_multi_query_can_be_disabled(monkeypatch):
    monkeypatch.setattr("filingcopilot.settings.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setenv("FC_RETRIEVAL_MULTI_QUERY", "false")
    assert Settings.from_env().retrieval_multi_query is False
