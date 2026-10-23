"""Fetch a small DocFinQA test subset for chunking experiments.

DocFinQA is MIT-licensed. The full dataset is ~4.7 GB because each row carries
a long 10-K/10-Q context. This fetcher grabs only the requested number of rows
from the test split and writes a local JSONL plus a manifest under gitignored
`evaluation/external/data/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

DATASET = "kensho/DocFinQA"
CONFIG = "default"
SPLIT = "test"
LICENSE = "MIT"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
DEFAULT_OUTPUT = Path("evaluation/external/data/docfinqa.jsonl")
DEFAULT_MANIFEST = Path("evaluation/external/data/docfinqa.manifest.json")


def fetch_rows(limit: int) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    page_size = 1
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        while len(rows) < limit:
            length = min(page_size, limit - len(rows))
            for attempt in range(4):
                try:
                    response = client.get(
                        ROWS_URL,
                        params={
                            "dataset": DATASET,
                            "config": CONFIG,
                            "split": SPLIT,
                            "offset": offset,
                            "length": length,
                        },
                    )
                    response.raise_for_status()
                    break
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if attempt == 3:
                        raise
                    wait = 2 ** attempt
                    print(f"retry {attempt + 1}/4 after {type(exc).__name__}; sleep {wait}s", flush=True)
                    time.sleep(wait)
            payload = response.json()
            page = [item["row"] for item in payload.get("rows", [])]
            if not page:
                break
            rows.extend(page)
            offset += len(page)
            print(f"fetched {len(rows)}/{limit} rows", flush=True)
            if len(page) < length:
                break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    rows = fetch_rows(args.limit)
    if not rows:
        raise SystemExit("No rows returned; check network access and the dataset endpoint")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    args.output.write_text(payload, encoding="utf-8")

    context_chars = [len(row.get("Context", "")) for row in rows]
    manifest = {
        "dataset": DATASET,
        "config": CONFIG,
        "split": SPLIT,
        "license": LICENSE,
        "source_url": f"https://huggingface.co/datasets/{DATASET}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "output": str(args.output),
        "fields": sorted(rows[0].keys()),
        "context_chars_min": min(context_chars),
        "context_chars_max": max(context_chars),
        "context_chars_avg": round(sum(context_chars) / len(context_chars)),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(rows)} rows -> {args.output}")
    print(f"context chars: min={manifest['context_chars_min']} max={manifest['context_chars_max']} avg={manifest['context_chars_avg']}")
    print(f"license: {LICENSE}; manifest -> {args.manifest}")


if __name__ == "__main__":
    main()
