
import pytest

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


@pytest.mark.parametrize("new_section", [False, True])
def test_page_break_does_not_cut_a_condition_away_from_its_sentence(new_section):
    opening = "Product transitions require planning across suppliers and customers throughout the year. "
    first = "We have experienced reduced demand for current generation products when customers anticipate"
    second = "new releases, and we may be unable to sell both generations at the same time."
    ending = " Changes in customer orders and channel inventory levels can also affect the timing of our revenue."
    heading = "<h2>Other business</h2>" if new_section else ""
    html = (f"<h2>Demand and supply</h2><p>{opening}{first}</p><hr>"
            f"{heading}<p>{second}{ending}</p>").encode()
    result = extract_passages(html, filing(), max_chars=300)
    complete = first + " " + second
    assert any(complete in p.text for p in result) is not new_section
    assert all(len(p.text) <= 300 for p in result)


def test_inline_bold_heading_sets_scope_for_following_paragraphs():
    title = "We may experience quality or supply problems."
    body = "Our hardware products are complex and can have defects in their design or manufacture. " * 2
    supplier = "There are limited suppliers for certain device components, and delayed supplies may reduce sales. " * 2
    html = ("<h2>Other matters</h2><p style='font-weight:bold'>"
            "<span style='font-weight:bold'>We</span>"
            "<span style='font-weight:bold'> may experience quality or supply problems.</span>"
            f"<span style='font-weight:normal'> {body}</span></p><p>{supplier}</p>").encode()
    passages = extract_passages(html, filing())
    assert any(supplier.strip() in p.text for p in passages)
    assert {p.section for p in passages if 'limited suppliers' in p.text} == {title}


def test_item_heading_in_layout_table_is_not_lost_or_taken_from_navigation():
    text = "We assess security threats and maintain procedures to protect our business operations and customer data. " * 2
    html = ("<h2>Prior section</h2><table><tr><td>Item 16K.</td>"
            "<td><a href='#security'>CYBERSECURITY</a></td></tr></table>"
            f"<p>{text}</p><table><tr><td>Item 16K.</td>"
            "<td><p style='font-weight:bold'>CYBERSECURITY</p></td></tr></table>"
            f"<p>{text} This is the actual section.</p>").encode()
    passages = extract_passages(html, filing())
    assert passages[0].section == "Prior section"
    assert passages[-1].section == "Item 16K. CYBERSECURITY"


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


@pytest.mark.parametrize("heading", [
    '<p style="font-weight:bold">{}</p>',
    '<p style="font-style:italic">{}</p>',
    '<h2>{}</h2>',
])
def test_repeated_short_headings_preserve_segment_scope(heading):
    currency = "Revenue and operating income included unfavorable currency effects of 2% and 3%, respectively."
    html = (
        heading.format("Regional Services")
        + "<p>This business provides regional services and support to commercial customers.</p>"
        + heading.format("Digital Products")
        + "<p>This business provides digital products to individual and enterprise customers.</p>"
        + heading.format("Regional Services")
        + f"<p>{currency}</p>"
        + heading.format("Digital Products")
        + f"<p>{currency}</p>"
    ).encode()
    passages = extract_passages(html, filing(), include_tables=False)
    matches = [p for p in passages if currency in p.text]
    assert {p.section for p in matches} == {"Regional Services", "Digital Products"}
    assert len(matches) == len({p.id for p in matches}) == 2
    assert all("This business provides" not in p.text for p in matches)


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_parser_upgrade_replaces_cached_passages_without_refetching_intact_bytes(tmp_path, monkeypatch, refresh_fails):
    import hashlib
    import json
    from datetime import date
    from types import SimpleNamespace

    import httpx

    from filingcopilot.corpus import Corpus
    from filingcopilot.discovery import CompanyDiscovery
    from filingcopilot.domain import DEFAULT_DOMAIN
    from filingcopilot.models import PARSER_VERSION, Passage
    from filingcopilot.settings import Settings

    raw = b"<h2>Regional Services</h2><p>A cached source whose original bytes remain intact after a parser upgrade.</p>"
    source = filing().model_copy(update={"content_sha256": hashlib.sha256(raw).hexdigest()})
    corpus = Corpus(tmp_path)
    old = Passage(id="old-scope", filing_id=source.id, ticker="AAPL", section="Annual report",
                  text="Currency movements affected the business.", source_url=source.source_url, locator="paragraph 1")
    current = extract_passages(raw, source, include_tables=True)[0]
    corpus.ingest(source, [], [old])
    with corpus.connect() as db:
        db.execute("UPDATE filings SET payload=? WHERE id=?", (
            json.dumps(source.model_copy(update={"parser_version": "old"}).model_dump(mode="json")), source.id))
    service = SimpleNamespace(settings=Settings(home=tmp_path), corpus=corpus,
                              refresh_domain=lambda: DEFAULT_DOMAIN, enriched=set())
    discovery = CompanyDiscovery(service)
    (discovery.fetcher.cache / (hashlib.sha256(source.source_url.encode()).hexdigest() + ".cache")).write_bytes(raw)
    refresh_flags = []

    def acquire(*args, **kwargs):
        refresh_flags.append(kwargs["force_refresh"])
        if refresh_fails:
            raise httpx.ReadTimeout("Latest submission metadata is temporarily unavailable")
        return [{"filing": source.model_dump(mode="json"), "facts": [],
                 "passages": [current.model_dump(mode="json")]}]

    monkeypatch.setattr("filingcopilot.discovery.acquire_company", acquire)
    result, _ = discovery._check_disclosures_locked("AAPL", date(2026, 10, 8), ("10-K",), 1,
                                                   "annual", fiscal_years=(2025,))
    assert refresh_flags == [False], "A stale parser is not a corrupt or missing SEC file"
    assert result["checked"] is not refresh_fails
    if refresh_fails:
        assert result["warning"], "Local parsing must not hide a failed freshness check"
    assert corpus.catalog(date(2026, 10, 8), "AAPL")[0]["parser_version"] == PARSER_VERSION
    assert corpus.evidence(current.id, date(2026, 10, 8))["section"] == "Regional Services"


def test_reparsing_preserves_typed_facts_and_rejects_changed_source(tools):
    corpus = tools.corpus
    source = Filing.model_validate(corpus.catalog(tools.as_of, "AAPL")[0])
    def typed_rows():
        with corpus.connect() as db:
            return {
                table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]
                for table in ("facts", "statement_facts")
            }

    before = typed_rows()
    assert before["facts"]
    passage = corpus.passages(tools.as_of, "AAPL")[0].model_copy(update={
        "id": "new-parser-passage", "text": "The preserved source now has its complete sentence and condition.",
    })
    corpus.replace_passages(source, [passage])
    assert typed_rows() == before
    assert [p.id for p in corpus.passages(tools.as_of, "AAPL")] == [passage.id]
    with pytest.raises(ValueError, match="unchanged local filing"):
        corpus.replace_passages(source.model_copy(update={"content_sha256": "b" * 64}), [passage])
    with pytest.raises(ValueError, match="same filing"):
        corpus.replace_passages(source, [passage.model_copy(update={"ticker": "MSFT"})])
    assert typed_rows() == before


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


def test_unrelated_company_refreshes_can_progress_concurrently(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from filingcopilot.discovery import CompanyDiscovery
    from filingcopilot.settings import Settings

    services = [CompanyDiscovery(SimpleNamespace(settings=Settings(home=tmp_path))) for _ in range(2)]
    together = threading.Barrier(2, timeout=2)
    def refresh(*args):
        together.wait()
        return "completed"
    for discovery in services:
        monkeypatch.setattr(discovery, "_check_disclosures_locked", refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(d._check_disclosures, ticker, None, (), 1, "annual")
                   for d, ticker in zip(services, ("AAPL", "MSFT"), strict=True)]
        assert [f.result() for f in futures] == ["completed", "completed"]


def test_same_issuer_refreshes_stay_serialized_across_services(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from filingcopilot.discovery import CompanyDiscovery
    from filingcopilot.settings import Settings

    services = [CompanyDiscovery(SimpleNamespace(settings=Settings(home=tmp_path))) for _ in range(2)]
    ready = threading.Barrier(2, timeout=2)
    active, maximum = 0, 0
    guard = threading.Lock()
    def refresh(*args):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with guard:
            active -= 1
    for discovery in services:
        monkeypatch.setattr(discovery, "_check_disclosures_locked", refresh)
    def run(discovery):
        ready.wait()
        discovery._check_disclosures("AAPL", None, (), 1, "annual")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, services))
    assert maximum == 1
