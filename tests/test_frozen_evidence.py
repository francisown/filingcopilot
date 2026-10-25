from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = {
    "third-round-cold-PDD-2026-10-11.json": "655073f641a20b4bbe37b1bf2c26610a764fe778710ab988b69bc169a00991a1",
    "third-round-cold-BABA-2026-10-11.json": "ad180fec38bb0b394fce5824258173f2b3d34a33abd2ee3680f3064f34df987d",
    "retrieval-frozen-diagnosis-2026-10-11.json": "036053e14addafe0b0dc63ce139b671e0ef5db04bc9048798ac08d84f1ead16c",
}


def test_referenced_frozen_evidence_is_tracked_and_unchanged() -> None:
    for name, expected in FROZEN.items():
        path = ROOT / "artifacts" / name
        assert path.is_file(), f"restore frozen evidence consumed by scripts: {name}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected, f"frozen evidence changed: {name}"


def test_frozen_evidence_keeps_hashes_and_negative_candidates() -> None:
    diagnosis = json.loads(
        (ROOT / "artifacts" / "retrieval-frozen-diagnosis-2026-10-11.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnosis["schema"] == "filingcopilot.retrieval-diagnosis/v1"
    assert len(diagnosis["gold_sha256"]) == 64
    assert len(diagnosis["source_snapshot_sha256"]) == 64
    assert diagnosis["full_set"] is True
    assert diagnosis["variants"]

    for ticker in ("PDD", "BABA"):
        replay = json.loads(
            (ROOT / "artifacts" / f"third-round-cold-{ticker}-2026-10-11.json").read_text(
                encoding="utf-8-sig"
            )
        )
        assert replay["ticker"] == ticker
        assert replay["sources"]
        assert all(len(source["content_sha256"]) == 64 for source in replay["sources"])
