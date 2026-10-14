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
