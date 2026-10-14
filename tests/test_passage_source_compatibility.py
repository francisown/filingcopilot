"""Source replay must accept legacy optional defaults and still reject changed content."""

import hashlib

import pytest

from filingcopilot.audit import _check_raw_sources
from filingcopilot.ingestion import extract_passages
from filingcopilot.models import Filing

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def _case(tmp_path, html, *, table=False):
    filing = Filing(id="AAPL:source", ticker="AAPL", company="Apple", cik="0000320193",
                    accession="0000320193-25-000079", form="10-K", filed="2025-10-31",
                    period_end="2025-09-27", source_url="https://www.sec.gov/Archives/source.htm",
                    content_sha256=hashlib.sha256(html).hexdigest())
    (tmp_path / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")).write_bytes(html)
    passages = extract_passages(html, filing, include_tables=table)
    passage = next(p for p in passages if bool(p.table_context) == table)
    return filing, {**passage.model_dump(mode="json"), "kind": "passage", "filing": filing.model_dump(mode="json")}


def _replay(tmp_path, filing, passage):
    checks = []
    _check_raw_sources({passage["id"]: passage}, {filing.id: filing}, tmp_path,
                      lambda code, condition, detail, evidence_id=None: checks.append((code, condition)),
                      {"filings_checked": 0, "facts_checked": 0, "missing": 0}, None)
    assert ("raw_filing_hash", True) in checks
    return next(condition for code, condition in checks if code == "raw_passage")


@pytest.mark.parametrize("change,expected", [
    ("legacy_default_omitted", True), ("explicit_null", True), ("changed_text", False),
    ("changed_locator", False), ("invented_table_context", False), ("unknown_field", False),
])
def test_legacy_default_compatibility_keeps_exact_source_checks(tmp_path, change, expected):
    html = b"<html><p>The company relies on manufacturing partners. Supply disruptions could adversely affect operations. This disclosure concerns potential events rather than realized losses.</p></html>"
    filing, passage = _case(tmp_path, html)
    if change == "legacy_default_omitted":
        passage.pop("table_context")
    elif change == "changed_text":
        passage["text"] += " Unsupported source addition."
    elif change == "changed_locator":
        passage["locator"] = "wrong source position"
    elif change == "invented_table_context":
        passage["table_context"] = {"invented": "header"}
    elif change == "unknown_field":
        passage["unrecognized"] = "do not discard this field"
    assert _replay(tmp_path, filing, passage) is expected


def test_table_passage_replays_header_context_from_raw_source(tmp_path):
    html = b"<html><table id='income'><caption>USD millions</caption><tr><th>Metric</th><th>FY2025</th><th>FY2024</th></tr><tr><td>Revenue</td><td>200</td><td>100</td></tr></table></html>"
    filing, passage = _case(tmp_path, html, table=True)
    assert _replay(tmp_path, filing, passage) is True
    passage["table_context"] = None
    assert _replay(tmp_path, filing, passage) is False
