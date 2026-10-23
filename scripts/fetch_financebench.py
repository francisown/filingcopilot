"""Fetch the public FinanceBench subset into a local JSONL file.

The Hugging Face dataset card marks FinanceBench as CC-BY-NC-4.0. This script
downloads only the public rows, writes them under a gitignored directory, and
records the source, license, row count and SHA256 in a manifest.

No Hugging Face token, pandas, pyarrow, or datasets library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

DATASET = "PatronusAI/financebench"
CONFIG = "default"
SPLIT = "train"
LICENSE = "CC-BY-NC-4.0"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
DEFAULT_OUTPUT = Path("evaluation/external/data/financebench.jsonl")
DEFAULT_MANIFEST = Path("evaluation/external/data/financebench.manifest.json")


def fetch_rows(limit: int | None = None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    page_size = 100
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        while True:
            length = page_size if limit is None else min(page_size, limit - len(rows))
            if length <= 0:
                break
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
            payload = response.json()
            page = [item["row"] for item in payload.get("rows", [])]
            if not page:
                break
            rows.extend(page)
            offset += len(page)
            total = payload.get("num_rows_total")
            print(f"fetched {len(rows)}/{total if total is not None else '?'} rows", flush=True)
            if total is not None and len(rows) >= total:
                break
            if len(page) < length:
                break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int, help="Fetch only the first N rows")
    args = parser.parse_args()

    rows = fetch_rows(args.limit)
    if not rows:
        raise SystemExit("No rows returned; check network access and the dataset endpoint")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    args.output.write_text(payload, encoding="utf-8")

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
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    companies = sorted({str(row.get("company", "")) for row in rows if row.get("company")})
    question_types = sorted(
        {str(row.get("question_type", "")) for row in rows if row.get("question_type")}
    )
    print(f"wrote {len(rows)} rows -> {args.output}")
    print(f"companies: {', '.join(companies[:12])}{' ...' if len(companies) > 12 else ''}")
    print(f"question types: {', '.join(question_types)}")
    print(f"license: {LICENSE} (non-commercial; keep attribution)")
    print(f"manifest -> {args.manifest}")


if __name__ == "__main__":
    main()
