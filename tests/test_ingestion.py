
from filingcopilot.ingestion import extract_facts, extract_passages
from filingcopilot.models import Filing


def filing():
    return Filing(
        id="AAPL:test",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="test-accession",
        form="10-K",
        filed="2025-10-31",
        period_end="2025-09-27",
        source_url="https://www.sec.gov/Archives/test.htm",
        content_sha256="a" * 64,
    )


def test_annual_extraction_excludes_quarter_future_and_other_accession():
    rows = [
        {
            "accn": "test-accession",
            "form": "10-K",
            "start": "2024-09-29",
            "end": "2025-09-27",
            "val": 100,
        },
        {
            "accn": "test-accession",
            "form": "10-K",
            "start": "2025-06-29",
            "end": "2025-09-27",
            "val": 25,
        },
        {"accn": "other", "form": "10-K", "start": "2024-09-29", "end": "2025-09-27", "val": 999},
        {
            "accn": "test-accession",
            "form": "10-K",
            "start": "2025-09-28",
            "end": "2026-09-26",
            "val": 888,
        },
    ]
    source = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}}
    result = extract_facts(source, filing())
    assert len(result) == 1 and result[0].value == 100
    assert result[0].fiscal_year == 2025
    assert "units.USD[0]" in result[0].locator and "test-accession" in result[0].locator


def test_narrative_extraction_retains_text_and_excludes_tables_and_hidden_data():
    text = "Manufacturing partners may face disruptions affecting our supply chain. " * 3
    html = (
        f"<html><ix:hidden><p>{'Hidden ' * 30}</p></ix:hidden>"
        f'<div id="risk"><p>{text}</p></div><table><tr><td><p>{"Table " * 30}</p>'
        "</td></tr></table></html>"
    ).encode()
    result = extract_passages(html, filing())
    assert len(result) == 1 and result[0].text == text.strip()
    assert result[0].source_url.endswith("#risk")


def test_short_sentences_and_risk_titles_survive_the_length_floor():
    """The 130-character floor must not delete one-line disclosures.

    Apple states its product drivers in single short sentences and Amazon titles
    each risk with a line that has no full stop. Dropping both made research
    report a content gap for text the filing contains.
    """
    html = """
    <html><body>
      <div><span>FORM 10-K</span></div>
      <div><span>UNITED STATES</span></div>
      <div><span>iPhone net sales were relatively flat during 2024 compared to 2023.</span></div>
      <div><span>We Could Be Harmed by Data Loss or Other Security Breaches</span></div>
      <div><span>The Company collects and stores large amounts of confidential data, and a
      breach could harm its reputation and materially affect results of operations.</span></div>
    </body></html>
    """
    passages = extract_passages(html, filing(), max_chars=1200, include_tables=False)
    texts = [p.text for p in passages]
    assert any("relatively flat during 2024" in text for text in texts)
    assert not any(text.strip() == "FORM 10-K" for text in texts)
    assert not any(text.strip() == "UNITED STATES" for text in texts)
    titled = [p for p in passages if "materially affect results of operations" in p.text]
    assert titled and titled[0].section == "We Could Be Harmed by Data Loss or Other Security Breaches"


def test_page_numbers_follow_the_printed_footer_when_the_offset_is_constant():
    """The break count includes the cover; the footer is what a reader sees."""
    html = """
    <html><body>
      <div><span>Cover page text that is long enough to become a passage, describing the registrant
      and the securities registered pursuant to Section 12(b) of the Exchange Act.</span></div>
      <hr style="page-break-after:always">
      <div><span>Apple Inc. | 2024 Form 10-K | 1</span></div>
      <div><span>First body page text that is long enough to become a real passage in the corpus,
      describing the business and the products the company sells to its customers.</span></div>
      <hr style="page-break-after:always">
      <div><span>Apple Inc. | 2024 Form 10-K | 2</span></div>
      <div><span>Second body page text that is long enough to become a real passage in the corpus,
      describing risk factors that could affect the company's results of operations.</span></div>
      <hr style="page-break-after:always">
      <div><span>Apple Inc. | 2024 Form 10-K | 3</span></div>
    </body></html>
    """
    passages = extract_passages(html, filing(), max_chars=1200, include_tables=False)
    pages = [p.page for p in passages if "First body page" in p.text or "Second body page" in p.text]
    assert pages and all(page is not None for page in pages)
    # Footer 1 sits on the page after the cover, so the body text prints as page 1.
    assert pages == [1, 2]


def test_corpus_stamps_the_parser_version_and_a_stale_one_is_detectable(tmp_path):
    """Old filings must be re-extracted after a parser fix, without anyone remembering."""
    import json as _json
    import sqlite3 as _sqlite3

    from filingcopilot.corpus import Corpus
    from filingcopilot.discovery import _parse_is_current
    from filingcopilot.models import PARSER_VERSION

    corpus = Corpus(tmp_path)
    f = filing()
    corpus.ingest(f, [], [])
    stored = _json.loads(
        _sqlite3.connect(tmp_path / "corpus.sqlite3")
        .execute("SELECT payload FROM filings WHERE id=?", (f.id,))
        .fetchone()[0]
    )
    assert stored["parser_version"] == PARSER_VERSION
    assert _parse_is_current(stored)
    stale = {**stored, "parser_version": "2020-01-01.0"}
    assert not _parse_is_current(stale)


def test_concurrent_writers_do_not_race_each_other(tmp_path):
    """A parallel evaluator may need to repair a filing while another thread reads."""
    import threading as _threading

    from filingcopilot.corpus import Corpus

    corpus = Corpus(tmp_path)
    errors = []

    def write(index):
        try:
            item = filing().model_copy(
                update={"id": f"AAPL:t{index}", "accession": f"acc-{index}"}
            )
            corpus.ingest(item, [], [])
        except Exception as exc:  # pragma: no cover - failure path is the assertion
            errors.append(exc)

    threads = [_threading.Thread(target=write, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    with corpus.connect() as db:
        assert db.execute("SELECT count(*) FROM filings").fetchone()[0] == 6
