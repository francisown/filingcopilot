from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from filingcopilot.corpus import Corpus
from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.models import Filing, Passage
from filingcopilot.retrieval import Retriever, rewrite_query
from filingcopilot.settings import Settings

AS_OF = date(2026, 10, 8)


def _retriever(tmp_path: Path) -> Retriever:
    corpus = Corpus(tmp_path)
    filing = Filing(
        id="AAPL:2025",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-25-000079",
        form="10-K",
        filed=date(2025, 10, 31),
        period_end=date(2025, 9, 27),
        source_url="https://www.sec.gov/Archives/test.htm",
        content_sha256="a" * 64,
    )
    passages = [
        Passage(
            id="p-risk",
            filing_id=filing.id,
            ticker="AAPL",
            section="Risk factors",
            text="Supply chain disruption could reduce revenue and demand. Customers may defer purchases.",
            source_url=filing.source_url,
            locator="risk paragraph",
        ),
        Passage(
            id="p-business",
            filing_id=filing.id,
            ticker="AAPL",
            section="Item 1. Business",
            text="The company sells products and services to enterprise customers.",
            source_url=filing.source_url,
            locator="business paragraph",
        ),
        Passage(
            id="p-mda",
            filing_id=filing.id,
            ticker="AAPL",
            section="Item 7. Management's Discussion and Analysis",
            text="Revenue increased due to higher services revenue.",
            source_url=filing.source_url,
            locator="mda paragraph",
        ),
        Passage(
            id="p-cash",
            filing_id=filing.id,
            ticker="AAPL",
            section="Item 8. Financial Statements",
            text="Cash flow from operating activities was positive.",
            source_url=filing.source_url,
            locator="cash paragraph",
        ),
    ]
    corpus.ingest(filing, [], passages)
    return Retriever(
        corpus,
        Settings(home=tmp_path, embedding_mode="off", rerank_mode="coverage"),
    )


def test_coverage_mode_uses_rare_terms_and_section_prior(tmp_path):
    retriever = _retriever(tmp_path)
    result = retriever.search(
        "What risks affect revenue from supply chain disruption?",
        AS_OF,
        ticker="AAPL",
        k=3,
        diagnostics=True,
    )
    assert result["backend"] == "fts5+coverage"
    assert result["hits"][0]["passage"]["id"] == "p-risk"
    coverage = result["hits"][0]["legs"]["coverage"]
    assert coverage["section_prior"] == 1.0
    assert "supply" in coverage["matched_terms"]
    assert "p-risk" in result["retrieval_diagnostics"]["candidate_ids"]


def test_coverage_mode_rejects_invalid_section_weight(tmp_path):
    retriever = _retriever(tmp_path)
    with pytest.raises(ValueError, match="coverage section weight"):
        retriever.search("risk", AS_OF, ticker="AAPL", coverage_section_weight=-0.1)


def test_coverage_mode_reranks_only_bounded_candidates(tmp_path):
    retriever = _retriever(tmp_path)
    result = retriever.search(
        "What risks affect revenue from supply chain disruption?",
        AS_OF,
        ticker="AAPL",
        k=3,
        candidate_limit=2,
        diagnostics=True,
    )
    diagnostics = result["retrieval_diagnostics"]
    assert result["backend"] == "fts5+coverage"
    assert diagnostics["candidate_limit_per_leg"] == 2
    assert diagnostics["coverage_candidates"] <= 2
    assert len(diagnostics["candidate_ids"]) <= 2


def test_coverage_mode_uses_dense_candidates_when_available(tmp_path, monkeypatch):
    retriever = _retriever(tmp_path)
    retriever.settings = Settings(
        home=tmp_path, embedding_mode="local", rerank_mode="coverage"
    )

    class FakeEmbedder:
        def query_embed(self, query):
            return [np.array([1.0, 0.0], dtype=np.float32)]

    monkeypatch.setattr(
        retriever, "_vectors", lambda passages: np.ones((len(passages), 2), dtype=np.float32)
    )
    monkeypatch.setattr(retriever, "_embedder", lambda: FakeEmbedder())

    result = retriever.search(
        "supply chain disruption",
        AS_OF,
        ticker="AAPL",
        k=3,
        diagnostics=True,
    )
    assert result["backend"] == "fts5+bge+coverage"
    assert result["retrieval_diagnostics"]["coverage_dense_candidates"] > 0


def test_bilingual_expansion_maps_customers_to_english():
    expanded = rewrite_query(
        "业务、产品、服务和客户", DEFAULT_DOMAIN.query_expansions, DEFAULT_DOMAIN
    )["expanded"]
    assert "customers" in expanded
    assert "products" in expanded
    assert "services" in expanded
