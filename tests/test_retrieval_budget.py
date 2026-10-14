"""Cold indexing and its mutex must observe the caller's research budget."""

import sqlite3
import threading

import numpy as np
import pytest
from test_retrieval_vectors import _passage

from filingcopilot.corpus import Corpus
from filingcopilot.execution import (
    ResearchCancelled,
    ResearchControl,
    ResearchDeadlineExceeded,
    research_scope,
)
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


class TimedEmbedder:
    def __init__(self, now):
        self.now = now
        self.batches = []

    def embed(self, texts, batch_size=16):
        self.batches.append(list(texts))
        # Model preprocessing happens before the iterable yields. A whole-
        # corpus input therefore cannot be made interruptible by checking yields alone.
        self.now[0] += len(texts) / 4
        return [np.array([len(text), 1], dtype=np.float32) for text in texts]


@pytest.mark.parametrize("stopped", [False, True])
def test_vector_connection_is_closed_on_success_and_stop(tmp_path, monkeypatch, stopped, mock_embedding_version):
    retriever = Retriever(Corpus(tmp_path), Settings(home=tmp_path))
    connections = []
    connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        db = connect(*args, **kwargs)
        connections.append(db)
        return db

    class Embedder:
        def embed(self, texts, **kwargs):
            if stopped:
                raise ResearchDeadlineExceeded()
            return [np.array([1, 2], dtype=np.float32) for _ in texts]

    retriever._embedder = lambda: Embedder()
    monkeypatch.setattr("filingcopilot.retrieval.sqlite3.connect", tracked_connect)
    passages = [_passage("p1", "financial risk")]
    if stopped:
        with pytest.raises(ResearchDeadlineExceeded):
            retriever._vectors(passages)
    else:
        assert retriever._vectors(passages).shape == (1, 2)
    assert connections
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")


def test_cold_scope_stops_between_batches_and_keeps_completed_cache(tmp_path, monkeypatch, mock_embedding_version):
    now = [100.0]
    monkeypatch.setattr("filingcopilot.execution.time.monotonic", lambda: now[0])
    retriever = Retriever(Corpus(tmp_path), Settings(home=tmp_path))
    model = TimedEmbedder(now)
    retriever._embedder = lambda: model
    passages = [_passage("p" + str(i), "risk disclosure " + str(i)) for i in range(40)]
    control = ResearchControl()
    control.start_budget(5)
    with research_scope(control), pytest.raises(ResearchDeadlineExceeded):
        retriever._vectors(passages)
    assert now[0] <= 108, "indexing processed the entire cold scope after its deadline"
    with sqlite3.connect(tmp_path / "vectors" / "embeddings.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 16

    resumed = Retriever(Corpus(tmp_path), Settings(home=tmp_path))
    second = TimedEmbedder(now)
    resumed._embedder = lambda: second
    assert resumed._vectors(passages).shape == (40, 2)
    assert sum(len(batch) for batch in second.batches) == 24


def test_cancelled_embedding_is_not_swallowed_as_a_permanent_model_failure(tools):
    control = ResearchControl()

    def cancelled_model():
        control.cancel()
        raise ResearchCancelled()

    tools.retriever.settings = Settings(home=tools.corpus.home)
    tools.retriever._embedder = cancelled_model
    with research_scope(control), pytest.raises(ResearchCancelled):
        tools.retriever.search("manufacturing", tools.as_of, "AAPL")
    assert tools.retriever.model_error is None


def test_cancelled_waiter_leaves_index_mutex_while_owner_still_holds_it(tools, thread_timeout):
    control = ResearchControl()
    owner = threading.Lock()
    owner.acquire()
    entered = threading.Event()
    done = threading.Event()
    outcomes = []

    class ObservedLock:
        def acquire(self, **kwargs):
            entered.set()
            return owner.acquire(**kwargs)

        def release(self):
            owner.release()

        def __enter__(self):
            self.acquire()

        def __exit__(self, *_):
            self.release()

    tools.retriever.lock = ObservedLock()
    tools.retriever.settings = Settings(home=tools.corpus.home)
    tools.retriever._embedder = lambda: None

    def lookup():
        try:
            with research_scope(control):
                tools.retriever.search("manufacturing", tools.as_of, "AAPL")
        except Exception as exc:
            outcomes.append(type(exc))
        finally:
            done.set()

    worker = threading.Thread(target=lookup)
    worker.start()
    try:
        assert entered.wait(thread_timeout)
        control.cancel()
        assert done.wait(thread_timeout), "cancelled lookup remained blocked by a different request"
        assert outcomes == [ResearchCancelled]
    finally:
        owner.release()
        worker.join(thread_timeout)
