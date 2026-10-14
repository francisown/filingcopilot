"""Structural CI gate for the corpus-anchored retrieval gold set."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    payload = json.loads(Path("evaluation/retrieval_gold_v2.json").read_text(encoding="utf-8"))
    items = payload.get("items", [])
    assert payload.get("schema") == "filingcopilot.retrieval-gold/v2"
    assert len(items) >= 200 and payload.get("count") == len(items)
    assert {item.get("language") for item in items} == {"en", "zh"}
    assert len({item.get("ticker") for item in items}) >= 5
    ids = [item.get("expected_passage_ids", [""])[0] for item in items]
    assert all(value.startswith("passage-") for value in ids)
    assert not any(value.startswith("gold-") for value in ids)
    print(f"retrieval gold v2 passed: {len(items)} corpus-anchored questions")


if __name__ == "__main__":
    main()
