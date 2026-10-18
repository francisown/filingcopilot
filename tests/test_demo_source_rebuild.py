import hashlib
import json
from copy import deepcopy
from datetime import date

import pytest

from scripts import rebuild_demo_sources as rebuild_tool


def fixture_entry():
    return {
        "filing": {"id": "MSFT:accession", "ticker": "MSFT", "cik": "0000789019",
                   "period_end": "2026-06-30", "source_url": "https://www.sec.gov/Archives/test.htm",
                   "content_sha256": "old"},
        "facts": [{"id": "fact", "value": "100", "unit": "USD"}],
        "passages": [{"id": "passage", "text": "Observed source text."}],
    }


def setup_rebuild(tmp_path, monkeypatch, mutate=lambda entry: None):
    old = fixture_entry()
    new = deepcopy(old)
    new["filing"]["content_sha256"] = hashlib.sha256(b"source").hexdigest()
    new["passages"][0]["context_id"] = None
    new["facts"].append({"id": "new-fact", "value": "20", "unit": "USD"})
    mutate(new)
    monkeypatch.setattr(rebuild_tool, "acquire_company", lambda *a, **kw: [new])
    monkeypatch.setattr(rebuild_tool.SecFetcher, "fetch", lambda self, url, offline: b"source")
    snapshot = json.dumps({"acquired_at": "original", "filings": [old, {"filing": {"id": "other"}}]}).encode()
    return lambda ids=None: rebuild_tool.rebuild(snapshot, tmp_path, ids or ["MSFT:accession"], date(2026, 10, 8))


def test_rebuild_preserves_original_observations_and_unrelated_filings(tmp_path, monkeypatch):
    output, receipt = setup_rebuild(tmp_path, monkeypatch)()
    result = json.loads(output)
    assert result["acquired_at"] == "original"
    assert result["filings"][1] == {"filing": {"id": "other"}}
    assert result["filings"][0]["facts"][0] == fixture_entry()["facts"][0]
    assert len(result["filings"][0]["facts"]) == 1
    assert result["filings"][0]["passages"] == fixture_entry()["passages"]
    assert receipt["filings"][0]["extracted_counts"]["facts"] == 2
    assert receipt["network"] is False
    assert receipt["output_snapshot_sha256"] == hashlib.sha256(output).hexdigest()


@pytest.mark.parametrize("mutation", [
    lambda entry: entry["facts"][0].update(value="101"),
    lambda entry: entry["passages"][0].update(text="Different text."),
    lambda entry: entry["passages"].clear(),
    lambda entry: entry["facts"].append(deepcopy(entry["facts"][0])),
    lambda entry: entry["filing"].update(period_end="2025-06-30"),
    lambda entry: entry["filing"].update(id="MSFT:other-accession"),
])
def test_rebuild_rejects_changed_or_missing_evidence(tmp_path, monkeypatch, mutation):
    rebuild = setup_rebuild(tmp_path, monkeypatch, mutation)
    with pytest.raises(ValueError):
        rebuild()


@pytest.mark.parametrize("ids", [["unknown"], ["MSFT:accession", "MSFT:accession"]])
def test_rebuild_rejects_invalid_selection(tmp_path, monkeypatch, ids):
    with pytest.raises(ValueError, match="unique filing IDs"):
        setup_rebuild(tmp_path, monkeypatch)(ids)
