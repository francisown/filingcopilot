import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.watchlist import WatchlistSelection

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.fixture
def service(tmp_path):
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))


def test_saved_list_is_idempotent_ordered_and_survives_service_restart(service):
    first = service.watchlist.add(" aapl ")
    added = first["items"][0]["added_at"]
    service.watchlist.add("MSFT")
    service.watchlist.lookup = lambda *a, **k: pytest.fail("duplicate should not resolve again")
    result = service.watchlist.add("AAPL")
    assert [i["ticker"] for i in result["items"]] == ["AAPL", "MSFT"]
    assert result["items"][0]["added_at"] == added
    reopened = FilingCopilot(service.settings).watchlist.items()
    assert reopened == result
    assert service.corpus.recent_runs() == []


def test_explicit_listing_and_annual_capabilities_stay_distinct(service):
    service.watchlist.add("TSM")
    result = service.watchlist.add("2330.TW")
    assert [i["currency"] for i in result["items"]] == ["USD", "TWD"]
    assert result["items"][0]["capabilities"]["financial_trend"]["enabled"]
    assert not result["items"][1]["capabilities"]["financial_trend"]["enabled"]
    service.watchlist.remove("tsm")
    service.watchlist.remove("TSM")
    assert [i["ticker"] for i in service.watchlist.items()["items"]] == ["2330.TW"]


@pytest.mark.parametrize("ticker", ["../secret", "AAPL?token=abc", "<script>", "", "a" * 16])
def test_request_refuses_unsafe_or_non_symbol_inputs(ticker):
    with pytest.raises(ValueError):
        WatchlistSelection(ticker=ticker)


def test_lookup_cannot_substitute_another_listing(service):
    service.watchlist.lookup = lambda *a, **k: {"candidates": [{"ticker": "TSM"}]}
    with pytest.raises(ValueError, match="上市代码"):
        service.watchlist.add("TSMC34.SA")
    assert not service.watchlist.items()["items"]


def test_api_stores_server_identity_not_client_names_or_quotes(service):
    with TestClient(create_app(service)) as client:
        assert client.get("/api/watchlist").json()["items"] == []
        assert (
            client.post(
                "/api/watchlist", json={"ticker": "AAPL", "name": "other issuer"}
            ).status_code
            == 422
        )
        result = client.post("/api/watchlist", json={"ticker": " aapl "}).json()
        assert result["items"][0]["name"] == "Apple Inc."
        assert "quote_value" not in result["items"][0]
        assert client.get("/api/health").json()["watchlist"]
        assert client.delete("/api/watchlist/AAPL").json()["items"] == []


def test_api_failed_lookup_preserves_prior_list_and_sanitizes_url(service):
    service.watchlist.add("AAPL")

    def failure(*a, **k):
        raise httpx.ConnectError("private-url?api_key=do-not-display")

    service.watchlist.lookup = failure
    with TestClient(create_app(service)) as client:
        response = client.post("/api/watchlist", json={"ticker": "MSFT"})
        assert response.status_code == 502
        assert "do-not-display" not in response.text and "private-url" not in response.text
        assert [i["ticker"] for i in client.get("/api/watchlist").json()["items"]] == ["AAPL"]


def test_capacity_rechecked_across_parallel_resolvers(service, monkeypatch, thread_timeout):
    import filingcopilot.watchlist as module

    monkeypatch.setattr(module, "LIMIT", 2)
    service.watchlist.add("AAPL")
    barrier = Barrier(2)

    def lookup(ticker, **kwargs):
        barrier.wait(timeout=thread_timeout)
        return {"candidates": [{"ticker": ticker, "name": ticker}]}

    service.watchlist.lookup = lookup
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.watchlist.add, t) for t in ("MSFT", "NVDA")]
        outcomes = [f.exception(timeout=thread_timeout * 2) for f in futures]
    assert sum(e is None for e in outcomes) == 1
    assert sum(isinstance(e, ValueError) for e in outcomes) == 1
    assert len(service.watchlist.items()["items"]) == 2
    assert len(service.watchlist.add("AAPL")["items"]) == 2


def test_watchlist_migration_does_not_modify_saved_research_payloads(service):
    with service.corpus.connect() as db:
        db.execute("INSERT INTO runs(id,payload) VALUES (?,?)", ("old", '{"original":1}'))
        db.execute("INSERT INTO run_packages VALUES (?,?)", ("old", '{"frozen":2}'))
        db.execute("DROP TABLE watchlist")
        db.execute("PRAGMA user_version=4")
    reopened = FilingCopilot(service.settings)
    with reopened.corpus.connect() as db:
        assert (
            db.execute("SELECT payload FROM runs WHERE id='old'").fetchone()[0] == '{"original":1}'
        )
        assert (
            db.execute("SELECT payload FROM run_packages WHERE run_id='old'").fetchone()[0]
            == '{"frozen":2}'
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
    assert json.dumps(reopened.watchlist.items()["items"]) == "[]"
