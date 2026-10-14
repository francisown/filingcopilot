from pathlib import Path

import numpy as np

from filingcopilot.corpus import Corpus
from filingcopilot.models import Passage
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings


class FakeEmbedder:
    def __init__(self):
        self.batches = []

    def embed(self, texts, batch_size=16):
        self.batches.append(list(texts))
        return [np.array([float(len(text)), float(index)], dtype=np.float32) for index, text in enumerate(texts)]


def _passage(identifier: str, text: str) -> Passage:
    return Passage(
        id=identifier,
        filing_id="filing",
        ticker="AAPL",
        section="Risk",
        text=text,
        source_url="https://example.invalid/filing",
        locator=identifier,
    )


def test_embedding_cache_reuses_overlap_between_scopes(tmp_path, mock_embedding_version):
    retriever = Retriever(Corpus(Path(tmp_path)), Settings(home=Path(tmp_path), embedding_mode="local"))
    fake = FakeEmbedder()
    retriever._embedder = lambda: fake
    first = [_passage("p1", "one"), _passage("p2", "two")]
    second = [_passage("p2", "two"), _passage("p3", "three")]

    first_vectors = retriever._vectors(first)
    second_vectors = retriever._vectors(second)

    assert [len(batch) for batch in fake.batches] == [2, 1]
    assert second_vectors.shape == (2, 2)
    assert second_vectors[0].tolist() == first_vectors[1].tolist()
    assert (Path(tmp_path) / "vectors" / "embeddings.sqlite3").exists()


def test_embedding_cache_survives_a_new_retriever(tmp_path, mock_embedding_version):
    home = Path(tmp_path)
    first = Retriever(Corpus(home), Settings(home=home, embedding_mode="local"))
    first_fake = FakeEmbedder()
    first._embedder = lambda: first_fake
    passages = [_passage("p1", "one"), _passage("p2", "two")]
    first._vectors(passages)

    second = Retriever(Corpus(home), Settings(home=home, embedding_mode="local"))
    second_fake = FakeEmbedder()
    second._embedder = lambda: second_fake
    values = second._vectors(passages)

    assert values.shape == (2, 2)
    assert second_fake.batches == []


def test_batches_reduce_padding_and_return_scope_in_source_order(tmp_path):
    retriever = Retriever(Corpus(tmp_path), Settings(home=tmp_path))
    passages = [_passage(str(i), 'x'*size) for i,size in enumerate([900,10,500,30,100,200])]
    fake = FakeEmbedder()
    batches = list(retriever._encode_batches(fake,passages))
    assert [len(p.text) for batch,_ in batches for p in batch] == [10,30,100,200,500,900]
    values = retriever._encode_scope(FakeEmbedder(),passages)
    assert values[:,0].tolist() == [900,10,500,30,100,200]
