import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from filingcopilot.local_models import verify_model_snapshot
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings
from scripts.download_retrieval_candidates import download_snapshot

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def snapshot(folder, model="test/model", revision="fixed-1", data=b"weights"):
    folder.mkdir(exist_ok=True)
    (folder / "model.onnx").write_bytes(data)
    manifest = {
        "model_name": model, "revision": revision,
        "sha256": {"model.onnx": hashlib.sha256(data).hexdigest()},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_local_model_snapshot_rejects_corruption_and_wrong_identity(tmp_path):
    snapshot(tmp_path)
    assert verify_model_snapshot(tmp_path, "test/model")["revision"] == "fixed-1"
    with pytest.raises(ValueError, match="requested model"):
        verify_model_snapshot(tmp_path, "other/model")
    (tmp_path / "model.onnx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        verify_model_snapshot(tmp_path, "test/model")


def test_local_model_manifest_cannot_read_outside_its_directory(tmp_path):
    folder = tmp_path / "model"
    folder.mkdir()
    secret = tmp_path / "not-model"
    secret.write_bytes(b"outside")
    (folder / "manifest.json").write_text(json.dumps({
        "model_name": "test/model", "revision": "fixed",
        "sha256": {"../not-model": hashlib.sha256(b"outside").hexdigest()},
    }))
    with pytest.raises(ValueError, match="escapes"):
        verify_model_snapshot(folder, "test/model")


def test_downloader_reuses_verified_files_and_repairs_changed_files(tmp_path):
    spec = {"repo": "publisher/model", "model_name": "test/model", "revision": "pinned-sha",
            "files": ["model.onnx"]}
    calls = []

    class Opener:
        def open(self, url, **kwargs):
            calls.append(url)
            return io.BytesIO(b"weights")

    first = download_snapshot(spec, tmp_path, Opener())
    assert first == download_snapshot(spec, tmp_path, Opener())
    assert len(calls) == 1 and "/resolve/pinned-sha/" in calls[0]
    (tmp_path / "model.onnx").write_bytes(b"corrupt")
    download_snapshot(spec, tmp_path, Opener())
    assert len(calls) == 2
    assert verify_model_snapshot(tmp_path, "test/model")


def test_incomplete_download_does_not_publish_ready_manifest(tmp_path):
    spec = {"repo": "publisher/model", "model_name": "test/model", "revision": "pinned-sha",
            "files": ["model.onnx", "tokenizer.json"]}

    class Opener:
        def open(self, url, **kwargs):
            if url.endswith("tokenizer.json"):
                raise ConnectionError("offline")
            return io.BytesIO(b"weights")

    with pytest.raises(ConnectionError):
        download_snapshot(spec, tmp_path, Opener())
    assert not (tmp_path / "manifest.json").exists()


def test_explicit_model_paths_separate_revision_vector_caches(tmp_path, mock_embedding_version):
    import numpy as np
    from test_retrieval_vectors import _passage

    from filingcopilot.corpus import Corpus

    model_dir = tmp_path / "model"
    snapshot(model_dir)

    class Embedder:
        def __init__(self, value):
            self.value = value

        def embed(self, texts, **kwargs):
            return [np.array([self.value, 1]) for _ in texts]

    first = Retriever(Corpus(tmp_path), Settings(home=tmp_path), model_path=model_dir)
    first._embedder = lambda: Embedder(1)
    passages = [_passage("p1", "risk")]
    assert first._vectors(passages)[0][0] == 1
    snapshot(model_dir, revision="fixed-2", data=b"changed-weights")
    second = Retriever(Corpus(tmp_path), Settings(home=tmp_path), model_path=model_dir)
    second._embedder = lambda: Embedder(2)
    assert second._vectors(passages)[0][0] == 2


def test_model_setup_rejects_unverified_local_directory_without_download(tools, monkeypatch, tmp_path):
    class ForbiddenNetwork:
        def __init__(self, **kwargs):
            pytest.fail("invalid explicit snapshot attempted SDK setup/download")

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=ForbiddenNetwork))
    retriever = Retriever(tools.corpus, Settings(home=Path(tmp_path)), model_path=tmp_path)
    assert retriever._embedder() is None
    assert retriever.model_error == "FileNotFoundError"
