"""Acquire real SEC filings and freeze the portable demo evidence snapshot."""

import json
from datetime import datetime, timezone
from pathlib import Path

from filingcopilot.ingestion import COMPANIES, SecFetcher, acquire_company
from filingcopilot.settings import Settings

settings = Settings.from_env()
fetcher = SecFetcher(settings.home / "sec-cache", settings.sec_user_agent)
entries = []
for ticker in COMPANIES:
    print("Acquiring", ticker, flush=True)
    items = acquire_company(fetcher, ticker, years=2)
    entries.extend(items)
    print(
        ticker,
        "filings",
        len(items),
        "facts",
        sum(len(x["facts"]) for x in items),
        "passages",
        sum(len(x["passages"]) for x in items),
        flush=True,
    )
snapshot = {
    "source": "SEC EDGAR filing HTML and official CompanyFacts JSON",
    "acquired_at": datetime.now(timezone.utc).isoformat(),
    "filings": entries,
}
target = Path(__file__).resolve().parents[1] / "src/filingcopilot/samples/sec-demo.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
print("Frozen snapshot:", target, flush=True)
