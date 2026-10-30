import hashlib
import sqlite3
from datetime import date

import pytest

from filingcopilot.corpus import Corpus
from filingcopilot.domain import Company


def test_frozen_corpus_reads_leave_all_database_bytes_unchanged(tools):
    path = tools.corpus.path
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    frozen = Corpus(path.parent, readonly=True)
    assert len(frozen.passages(date(2026, 10, 8), 'AAPL')) == 1
    assert frozen.catalog(date(2026, 10, 8), 'AAPL')[0]['ticker'] == 'AAPL'
    assert frozen.lexical('supply', date(2026, 10, 8), 'AAPL', 5)
    with frozen.connect() as db, pytest.raises(sqlite3.OperationalError, match='readonly'):
        db.execute("DELETE FROM passages")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_readonly_missing_database_does_not_create_a_directory(tmp_path):
    absent = tmp_path/'missing'
    with pytest.raises(FileNotFoundError):
        Corpus(absent, readonly=True)
    assert not absent.exists()


def test_readonly_corpus_rejects_every_run_and_registry_write(tools):
    frozen = Corpus(tools.corpus.path.parent, readonly=True)
    with pytest.raises(RuntimeError, match="Readonly corpus"):
        frozen.save_run({"id": "readonly-run"})
    with pytest.raises(RuntimeError, match="Readonly corpus"):
        frozen.update_run({"id": "readonly-run"})
    with pytest.raises(RuntimeError, match="Readonly corpus"):
        frozen.register_company("ZZZZ", Company(cik="0000000001", name="Readonly"))


def test_readonly_write_connection_fails_before_opening_sqlite(tools):
    frozen = Corpus(tools.corpus.path.parent, readonly=True)
    with pytest.raises(RuntimeError, match="Readonly corpus"):
        with frozen._write_connect():
            pass
