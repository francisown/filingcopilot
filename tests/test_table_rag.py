from datetime import date

import pytest
from test_cashflow import cash_fixture

from filingcopilot.ingestion import extract_passages
from filingcopilot.models import Claim, Filing, Submission
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def filing():
    return Filing(
        id="AAPL:table",
        ticker="AAPL",
        company="Apple fixture",
        cik="0000320193",
        accession="table",
        form="10-K",
        filed="2026-07-31",
        period_end="2026-06-30",
        source_url="https://www.sec.gov/Archives/table.htm",
        content_sha256="a" * 64,
    )


HTML = b"""<html><h2>Revenue</h2><table id='revenue'><caption>USD in millions</caption>
<tr><th>Year</th><th>Revenue</th><th>Margin</th></tr>
<tr><td>2026</td><td>100</td><td>40%</td></tr>
</table><p>Revenue grew because demand improved. </p></html>"""


def test_table_passage_binds_header_and_unit_without_changing_legacy_default():
    source = filing()
    assert not [p for p in extract_passages(HTML, source) if p.table_context]
    passages = extract_passages(HTML, source, include_tables=True)
    tables = [p for p in passages if p.table_context]
    assert len(tables) == 1
    table = tables[0]
    assert table.table_context["headers"] == ["Year", "Revenue", "Margin"]
    assert table.table_context["unit"] == "USD in millions"
    assert "表头：Year；Revenue；Margin" in table.text
    assert "单位/说明：USD in millions" in table.text
    assert "header bound" in table.locator


def test_detached_table_amount_cannot_be_submitted_as_typed_fact(tmp_path):
    service, _ = cash_fixture(tmp_path)
    source = filing()
    table = next(p for p in extract_passages(HTML, source, include_tables=True) if p.table_context)
    item = {**table.model_dump(mode="json"), "kind": "passage", "filing": source.model_dump(mode="json")}
    tools = service.tools(date(2026, 10, 8))
    tools.ledger[table.id] = item
    checked, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[table.id])]),
        tools,
        "AAPL 2026 年营收是多少？",
    )
    assert not checked
    assert any("typed result" in error for error in errors)
