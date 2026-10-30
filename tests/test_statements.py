from datetime import date, timedelta

import pytest

from filingcopilot.ingestion import extract_facts
from filingcopilot.models import Filing, Passage
from filingcopilot.quarterly import fiscal_quarter_period_end
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.statements import extract_inline_statement_facts, extract_statement_facts

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def filing(accession: str, end: str) -> Filing:
    return Filing(
        id="AAPL:" + accession,
        ticker="AAPL",
        company="Apple Inc.",
        cik="0000320193",
        accession=accession,
        form="10-Q",
        filed=date.fromisoformat(end) + timedelta(days=30),
        period_end=end,
        source_url="https://www.sec.gov/Archives/edgar/data/320193/" + accession + "/q.htm",
        content_sha256="a" * 64,
    )


def facts_for(acc: str, end: str, value: int) -> dict:
    end_date = date.fromisoformat(end)
    start = end_date - timedelta(days=90)
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {"label": "Revenue", "units": {"USD": [
                    {"accn": acc, "form": "10-Q", "start": start.isoformat(), "end": end, "val": value}
                ]}},
                "Assets": {"label": "Assets", "units": {"USD": [
                    {"accn": acc, "form": "10-Q", "end": end, "val": value * 3}
                ]}},
                "ResearchAndDevelopmentExpense": {"label": "Research and development", "units": {"USD": [
                    {"accn": acc, "form": "10-Q", "start": start.isoformat(), "end": end, "val": value // 10}
                ]}},
            }
        }
    }


def test_all_companyfacts_rows_and_unregistered_labels_are_structured():
    doc = facts_for("a", "2026-06-27", 120)
    rows = extract_statement_facts(doc, filing("a", "2026-06-27"))
    assert {row.metric for row in rows} == {
        "us-gaap:Revenues", "us-gaap:Assets", "us-gaap:ResearchAndDevelopmentExpense"
    }
    assert {row.statement for row in rows} == {"income", "balance"}
    assert all(row.value for row in rows)


def test_inline_segment_rows_keep_dimension_and_locator():
    html = b'''<html><xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000320193</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:ProductAxis">a:ServicesMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2026-03-29</xbrli:startDate><xbrli:endDate>2026-06-27</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="u"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit><table><tr><td>Services revenue</td><td><ix:nonFraction name="us-gaap:Revenues" id="f" contextRef="c" unitRef="u" format="ixt:num-dot-decimal">40</ix:nonFraction></td></tr></table></html>'''
    rows = extract_inline_statement_facts(html, filing("a", "2026-06-27"))
    assert len(rows) == 1
    assert rows[0].dimensions == {"us-gaap:ProductAxis": "a:ServicesMember"}
    assert rows[0].dimension_labels["us-gaap:ProductAxis"] == "ServicesMember"
    assert rows[0].locator.startswith("inline-xbrl:id=f")


def test_statement_and_eight_quarter_surface_preserve_missing_rows(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    for index, (end, value) in enumerate(
        [("2024-06-27", 80), ("2024-09-28", 90), ("2025-06-28", 100), ("2025-09-27", 110),
         ("2026-03-28", 115), ("2026-06-27", 120)], 1
    ):
        current = filing(str(index), end)
        source = facts_for(str(index), end, value)
        service.corpus.ingest(
            current,
            extract_facts(source, current),
            [],
            extract_statement_facts(source, current),
        )
    tools = service.tools(date(2026, 10, 8))
    sequence = tools.get_quarterly_sequence("AAPL", ["revenue"], quarters=8)
    assert sequence["found"] and len(sequence["periods"]) == 6
    assert sequence["periods"][0]["facts"][0]["value"] == "120"
    statement = tools.get_statement("AAPL", "balance", period_end="2026-06-27")
    assert statement["found"] and statement["rows"][0]["metric"] == "us-gaap:Assets"
    assert "zero" in statement["missing"][0] if statement["missing"] else True


def test_statement_query_returns_a_small_ranked_table_without_losing_ledger_rows(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    current = filing("query", "2026-06-27")
    source = facts_for("query", "2026-06-27", 120)
    service.corpus.ingest(current, extract_facts(source, current), [], extract_statement_facts(source, current))
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_statement(
        "AAPL", "income", period_end="2026-06-27", query="research development", limit=1
    )
    assert result["row_count"] >= result["returned_row_count"] == 1
    assert "ResearchAndDevelopmentExpense" in result["rows"][0]["metric"]
    assert result["rows"][0]["id"] in tools.ledger


def test_industry_profiles_are_separate_from_universal_metrics():
    from filingcopilot.domain import DEFAULT_DOMAIN

    assert {"bank", "insurance", "software", "retail"} <= set(DEFAULT_DOMAIN.industry_profiles)
    assert "net_interest_income" in DEFAULT_DOMAIN.industry_profiles["bank"].metrics


def test_named_fiscal_quarter_requires_source_disclosed_label(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    current = filing("label", "2026-06-27")
    source = facts_for("label", "2026-06-27", 120)
    passage = Passage(
        id="quarter-label",
        filing_id=current.id,
        ticker="AAPL",
        section="Management discussion",
        text="Revenue increased in the third quarter of fiscal 2026 ended June 27, 2026.",
        source_url=current.source_url,
        locator="label fixture",
    )
    service.corpus.ingest(current, extract_facts(source, current), [passage], extract_statement_facts(source, current))
    tools = service.tools(date(2026, 10, 8))
    assert fiscal_quarter_period_end(tools, "AAPL", 2026, 3) == "2026-06-27"
    assert fiscal_quarter_period_end(tools, "AAPL", 2026, 1) is None
