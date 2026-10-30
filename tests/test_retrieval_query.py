from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.models import Filing, Passage
from filingcopilot.retrieval import Retriever
from filingcopilot.retrieval_query import evidence_slot_plan, retrieval_text
from filingcopilot.settings import Settings


def test_chinese_only_source_is_found_without_english_translation(tmp_path):
    corpus = Corpus(Path(tmp_path))
    filing = Filing(id="f", ticker="AAPL", company="Apple", cik="0000320193",
                    accession="0000320193-24-000001", form="10-K", filed=date(2024, 11, 1),
                    period_end=date(2024, 9, 30), source_url="https://www.sec.gov/Archives/a.htm",
                    content_sha256="a" * 64)
    source = Passage(id="p", filing_id="f", ticker="AAPL", section="风险", text="风险 供应链约束",
                     source_url=filing.source_url, locator="paragraph 1")
    corpus.ingest(filing, [], [source])
    retriever = Retriever(corpus, Settings(home=Path(tmp_path), embedding_mode="off"))
    assert [hit["passage"]["id"] for hit in retriever.search("风险", date(2024, 12, 1), "AAPL")["hits"]] == ["p"]


def test_unsegmented_chinese_search_stays_within_publication_and_section_scope(tmp_path):
    corpus = Corpus(Path(tmp_path))
    filing = Filing(id="f", ticker="AAPL", company="Apple", cik="0000320193",
                    accession="0000320193-24-000001", form="10-K", filed=date(2024, 11, 1),
                    period_end=date(2024, 9, 30), source_url="https://www.sec.gov/Archives/a.htm",
                    content_sha256="a" * 64)
    passages = [Passage(id=f"p{i:02d}", filing_id="f", ticker="AAPL",
                        section="附注" if i < 12 else "经营", text="供应链风险持续存在",
                        source_url=filing.source_url, locator=f"paragraph {i}") for i in range(20)]
    corpus.ingest(filing, [], passages)
    retriever = Retriever(corpus, Settings(home=Path(tmp_path), embedding_mode="off"))
    search = retriever.search("风险", date(2024, 12, 1), "AAPL", k=99, section_title="附注", diagnostics=True)
    assert len(search["hits"]) == 8
    assert len(search["retrieval_diagnostics"]["candidate_ids"]) == 12
    assert all(hit["passage"]["section"] == "附注" for hit in search["hits"])
    assert not retriever.search("风险", date(2024, 10, 1), "AAPL")["hits"]
    assert not retriever.search("风险", date(2024, 12, 1), "MSFT")["hits"]
    assert "retrieval_diagnostics" not in retriever.search("风险", date(2024, 12, 1), "AAPL")


def test_scoped_rewrite_removes_request_boilerplate_but_retains_negation():
    text = retrieval_text("Review Apple's FY2024 annual filing: where does it discuss debt not repaid?", DEFAULT_DOMAIN, "AAPL", 2024)
    assert text == "debt not repaid"
    assert "Apple" in retrieval_text("Apple FY2024 debt", DEFAULT_DOMAIN, None, None)
    assert "FY2024" in retrieval_text("Apple FY2024 debt", DEFAULT_DOMAIN, "AAPL", None)


def test_unrecognized_scope_does_not_strip_another_company_or_year():
    text = retrieval_text("Microsoft FY2023 China demand", DEFAULT_DOMAIN, "AAPL", 2024)
    assert all(term in text for term in ["Microsoft", "FY2023", "China", "demand"])


def test_evidence_slot_plan_is_deterministic_and_question_specific():
    risk = evidence_slot_plan("What customer concentration risks did Apple disclose in FY2024?")
    cause = evidence_slot_plan("Why did operating margin decline in FY2024?")
    assert [slot["id"] for slot in risk] == ["period", "risk_disclosure", "customer_concentration"]
    assert [slot["id"] for slot in cause] == ["period", "change", "driver"]
    assert evidence_slot_plan("Why did operating margin decline in FY2024?") == cause


def test_retrieval_keeps_period_in_evidence_slot_plan_after_scoping(tmp_path):
    corpus = Corpus(Path(tmp_path))
    filing = Filing(id="f", ticker="AAPL", company="Apple", cik="0000320193",
                    accession="0000320193-24-000001", form="10-K", filed=date(2024, 11, 1),
                    period_end=date(2024, 9, 30), source_url="https://www.sec.gov/Archives/a.htm",
                    content_sha256="a" * 64)
    source = Passage(id="p", filing_id="f", ticker="AAPL", section="风险",
                     text="Supply chain risk could affect revenue in FY2024.",
                     source_url=filing.source_url, locator="paragraph 1")
    corpus.ingest(filing, [], [source])
    result = Retriever(corpus, Settings(home=Path(tmp_path), embedding_mode="off")).search(
        "Apple FY2024 supply chain risks", date(2024, 12, 1), "AAPL"
    )
    assert result["evidence_slot_plan"][0] == {
        "id": "period", "required": True, "terms": ["2024"]
    }
