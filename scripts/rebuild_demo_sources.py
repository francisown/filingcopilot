"""Re-extract selected demo filings from an explicit SEC cache, without network.

Writes a new snapshot and provenance receipt. The caller reviews them before
replacing the bundled sample; existing workspaces and frozen runs are not changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.ingestion import SecFetcher, acquire_company


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retain_observations(old: dict, new: dict) -> None:
    """Reject changes to existing observations, allowing new optional metadata."""
    for kind in ("facts", "passages", "statement_facts"):
        rows = {row["id"]: row for row in new.get(kind, [])}
        if len(rows) != len(new.get(kind, [])):
            raise ValueError(f"Duplicate {kind} in re-extracted filing")
        for previous in old.get(kind, []):
            current = rows.get(previous["id"])
            if current is None or any(current.get(key) != value for key, value in previous.items()):
                raise ValueError(f"Existing {kind} changed or disappeared: {previous['id']}")


def rebuild(snapshot: bytes, cache: Path, filing_ids: list[str], as_of: date) -> tuple[bytes, dict]:
    source = json.loads(snapshot)
    result = deepcopy(source)
    entries = {entry["filing"]["id"]: entry for entry in result["filings"]}
    if not filing_ids or len(set(filing_ids)) != len(filing_ids) or not set(filing_ids) <= entries.keys():
        raise ValueError("Select unique filing IDs already in the snapshot")
    if not cache.is_dir():
        raise ValueError("An existing SEC cache is required; this tool never downloads sources")
    fetcher = SecFetcher(cache, "offline-snapshot-validation")
    records = []
    for identifier in filing_ids:
        old = entries[identifier]
        filing = old["filing"]
        acquired = acquire_company(
            fetcher, filing["ticker"], years=1, offline=True, domain=DEFAULT_DOMAIN,
            as_of=as_of, fiscal_years=(int(filing["period_end"][:4]),),
        )
        matches = [entry for entry in acquired if entry["filing"]["id"] == identifier]
        if len(matches) != 1:
            raise ValueError(f"Original accession not uniquely reacquired: {identifier}")
        extracted = matches[0]
        for key, value in filing.items():
            if key != "content_sha256" and extracted["filing"].get(key) != value:
                raise ValueError(f"Filing identity changed: {identifier}: {key}")
        _retain_observations(old, extracted)
        # Keep the demo's selection and field shape. Extra re-extracted tables
        # must not silently broaden retrieval or inflate every fresh database.
        # Every retained value comes from the newly extracted record; none is
        # carried forward merely because its old source hash was replaced.
        new = deepcopy(old)
        new["filing"] = extracted["filing"]
        for kind in ("facts", "passages", "statement_facts"):
            if kind in old:
                rows = {row["id"]: row for row in extracted.get(kind, [])}
                new[kind] = [{key: rows[previous["id"]].get(key) for key in previous}
                             for previous in old[kind]]
        urls = [
            filing["source_url"],
            f"https://data.sec.gov/submissions/CIK{filing['cik']}.json",
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{filing['cik']}.json",
        ]
        inputs = []
        for url in urls:
            raw = fetcher.fetch(url, offline=True)
            inputs.append({"url": url, "sha256": _digest(raw), "bytes": len(raw)})
        records.append({
            "filing_id": identifier,
            "old_content_sha256": filing["content_sha256"],
            "new_content_sha256": new["filing"]["content_sha256"],
            "existing_observations_preserved": True,
            "selection": "Original demo IDs, order and field shape retained after full re-extraction",
            "extracted_counts": {kind: len(extracted.get(kind, []))
                                 for kind in ("facts", "passages", "statement_facts")},
            "counts": {kind: {"before": len(old.get(kind, [])), "after": len(new.get(kind, []))}
                       for kind in ("facts", "passages", "statement_facts")},
            "inputs": inputs,
        })
        old.clear()
        old.update(new)
    # Preserve the original acquisition date for unaffected filings. The receipt
    # records this rebuild separately; it does not pretend a new network fetch.
    output = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    receipt = {
        "schema": "filingcopilot.demo-source-rebuild/v1",
        "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "network": False,
        "method": "Full re-extraction from explicitly supplied cached official SEC responses",
        "input_snapshot_sha256": _digest(snapshot),
        "output_snapshot_sha256": _digest(output),
        "filings": records,
        "limitations": [
            "Hashes bind these cached bytes; they do not guarantee future SEC responses are identical.",
            "This updates the bundled seed only; existing databases and frozen evaluations are unchanged.",
        ],
    }
    return output, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--filing", action="append", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.receipt.exists() or args.output.resolve() == args.receipt.resolve():
        parser.error("Output and receipt must be distinct new files")
    output, receipt = rebuild(args.snapshot.read_bytes(), args.cache, args.filing, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Re-extracted {len(receipt['filings'])} filings; existing observations preserved")


if __name__ == "__main__":
    main()
