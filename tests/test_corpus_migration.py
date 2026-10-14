import json
import sqlite3
import threading
import time
from datetime import date

import pytest

from filingcopilot.corpus import Corpus

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_corpus_initialization_waits_for_transient_writer_lock(tmp_path):
    home = tmp_path / "corpus"
    Corpus(home)
    holder = sqlite3.connect(home / "corpus.sqlite3", timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    outcome = {}

    def initialize():
        try:
            Corpus(home)
        except Exception as exc:  # pragma: no cover - assertion reports the error
            outcome["error"] = exc
        else:
            outcome["ok"] = True

    thread = threading.Thread(target=initialize)
    thread.start()
    time.sleep(0.2)
    holder.rollback()
    holder.close()
    thread.join(timeout=6)

    assert not thread.is_alive()
    assert outcome == {"ok": True}


def test_old_database_migrates_without_losing_sources_or_history(tmp_path):
    path = tmp_path / "corpus.sqlite3"
    filing = {"id": "old", "ticker": "AAPL", "filed": "2025-10-31", "period_end": "2025-09-27"}
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE filings(id TEXT PRIMARY KEY,ticker TEXT,filed TEXT,payload TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO filings VALUES(?,?,?,?)",
            ("old", "AAPL", filing["filed"], json.dumps(filing)),
        )
        db.execute(
            "CREATE TABLE runs(id TEXT PRIMARY KEY,created TEXT DEFAULT CURRENT_TIMESTAMP,payload TEXT)"
        )
        db.execute("INSERT INTO runs(id,payload) VALUES('r1',?)", (json.dumps({"id": "r1"}),))
    corpus = Corpus(tmp_path)
    assert corpus.catalog(date(2026, 10, 8)) == [filing]
    assert corpus.recent_runs() == [{"id": "r1"}]
    for _ in range(2):
        Corpus(tmp_path)  # Idempotent after reopening, including multi-process startup.
    with corpus.connect() as db:
        row = db.execute("SELECT period_end,fiscal_year FROM filings").fetchone()
        assert tuple(row) == ("2025-09-27", 2025)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7


def test_scoped_queries_use_compound_indexes(tools):
    with tools.corpus.connect() as db:
        fact_plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM facts "
            "WHERE ticker=? AND metric=? AND fiscal_year=?",
            ("AAPL", "revenue", 2025),
        ).fetchall()
        scope, args = tools.corpus._scope(date(2026, 10, 8), "AAPL", 2025)
        passage_plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT p.payload FROM filings d "
            "JOIN passages p ON p.filing_id=d.id WHERE " + scope,
            args,
        ).fetchall()
    assert any("facts_scope" in row[3] for row in fact_plan)
    assert any("filings_scope" in row[3] for row in passage_plan)
    assert any("passages_filing" in row[3] for row in passage_plan)
