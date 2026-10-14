"""Score reviewer labels exported by ``export_semantic_blind_sample.py``.

The script intentionally does not infer labels from the model answer.  It
reports the labelled denominator, leaves incomplete samples as
``awaiting_human_labels``, and accepts both the English and Chinese label
spellings used by the review worksheet.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .export_semantic_blind_sample import REVIEW_KINDS, claim_fingerprint
except ImportError:  # direct script invocation
    from export_semantic_blind_sample import REVIEW_KINDS, claim_fingerprint

LABELS = {
    "support": "support",
    "supported": "support",
    "支持": "support",
    "unsupported": "unsupported",
    "不支持": "unsupported",
    "unclear": "unclear",
    "无法判断": "unclear",
    "不确定": "unclear",
}


def read_labels(payload, reviewer=None):
    """Accept legacy rows or a human self-declared offline-page export."""
    if isinstance(payload, list):
        return payload, reviewer
    if not isinstance(payload, dict) or payload.get('schema') != 'filingcopilot.human-label-export/v1':
        raise ValueError('Unsupported blind label input')
    name = payload.get('human_reviewer')
    if not isinstance(name, str) or not name.strip() or payload.get('human_attestation') != 'self_declared':
        raise ValueError('Explicit human reviewer declaration required')
    if reviewer and reviewer.strip() != name.strip():
        raise ValueError('Reviewer argument conflicts with human export')
    if not isinstance(payload.get('rows'), list):
        raise ValueError('Export rows must be an array')
    return payload['rows'], name.strip()


def score(rows: list[dict], *, review_key: dict | None = None, reviewer: str | None = None) -> dict:
    counts = Counter()
    invalid = []
    unlabeled = []
    seen = set()
    worksheet = {}
    labels = {}
    for row in rows:
        if not isinstance(row, dict):
            invalid.append("<not an object>")
            continue
        blind_id = row.get("blind_id", "")
        if not isinstance(blind_id, str):
            invalid.append("<non-string ID>")
            continue
        if not blind_id.strip() or blind_id in seen:
            invalid.append(blind_id or "<missing>")
            continue
        seen.add(blind_id)
        worksheet[blind_id] = row
        raw = row.get("label")
        if raw is None or not str(raw).strip():
            unlabeled.append(blind_id or "<missing>")
            continue
        normalized = LABELS.get(str(raw).strip().casefold())
        if normalized is None:
            invalid.append(blind_id or "<missing>")
            continue
        counts[normalized] += 1
        labels[blind_id] = normalized
    labeled = sum(counts.values())
    complete = bool(rows) and not unlabeled and not invalid
    calibration_errors = []
    reviewed = []
    if review_key is not None:
        if review_key.get("schema") != "filingcopilot.semantic-blind-key/v1":
            calibration_errors.append("unsupported review key schema")
        if "sample_size" in review_key:
            size = review_key["sample_size"]
            if type(size) is not int or size != len(rows):
                calibration_errors.append("review key sample size must match worksheet row count")
        entries = review_key.get("entries", [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            calibration_errors.append("review key entries must be objects")
            entries = []
        if any(not isinstance(entry.get("blind_id"), str) or not entry["blind_id"] for entry in entries):
            calibration_errors.append("review key entries need nonempty string IDs")
            entries = []
        key_ids = [entry.get("blind_id") for entry in entries]
        if len(set(key_ids)) != len(key_ids) or set(key_ids) != seen:
            calibration_errors.append("review key IDs must match worksheet IDs exactly once")
        lookup = {entry.get("blind_id"): entry for entry in entries}
        # Validate and count each worksheet ID once; never hash arbitrary JSON IDs
        # or count duplicate rows again in the calibration denominators.
        for blind_id, row in worksheet.items():
            entry = lookup.get(blind_id)
            if entry is None:
                continue
            if entry.get("claim_sha256") != claim_fingerprint(row):
                calibration_errors.append(f"claim or evidence changed: {row.get('blind_id')}")
                continue
            verdict = entry.get("independent_review_supported")
            if verdict is not None and type(verdict) is not bool:
                calibration_errors.append(f"invalid review verdict: {row.get('blind_id')}")
                continue
            if verdict is not None and row.get("kind") not in REVIEW_KINDS:
                calibration_errors.append(f"review verdict needs narrative or analysis: {blind_id}")
                continue
            label = labels.get(row.get("blind_id"))
            if verdict is not None and label in {"support", "unsupported"}:
                reviewed.append((label, verdict))
    unsupported_reviewed = sum(label == "unsupported" for label, _ in reviewed)
    missed = sum(label == "unsupported" and verdict for label, verdict in reviewed)
    approved = sum(verdict for _, verdict in reviewed)
    calibrated = complete and not calibration_errors and review_key is not None
    return {
        "schema": "filingcopilot.semantic-blind-labels/v1",
        "status": "invalid_calibration_key" if calibration_errors else "scored" if complete else "awaiting_human_labels",
        "sample_size": len(rows),
        "labeled_count": labeled,
        "unlabeled_count": len(unlabeled),
        "label_counts": dict(counts),
        "support_rate": round(counts["support"] / labeled, 6) if labeled else None,
        "unsupported_rate": round(counts["unsupported"] / labeled, 6) if labeled else None,
        "unclear_rate": round(counts["unclear"] / labeled, 6) if labeled else None,
        "unlabeled_ids": unlabeled,
        "invalid_ids": invalid,
        "human_reviewer": reviewer.strip() if reviewer else None,
        "human_acceptance_status": "awaiting_human_labels" if not complete else "reviewer_self_declared" if reviewer and reviewer.strip() else "awaiting_human_reviewer",
        "unit": "claim" if all(isinstance(row, dict) and "kind" in row for row in rows) and rows else "answer",
        "review_calibration": {
            "status": "invalid_key" if calibration_errors else "scored" if calibrated else "awaiting_human_labels" if review_key else "not_supplied",
            "conclusive_reviewed_claims": len(reviewed),
            "unsupported_reviewed_claims": unsupported_reviewed,
            "missed_unsupported_claims": missed,
            "review_miss_rate": round(missed / unsupported_reviewed, 6) if calibrated and unsupported_reviewed else None,
            "approved_reviewed_claims": approved,
            "approved_claim_error_rate": round(missed / approved, 6) if calibrated and approved else None,
            "errors": calibration_errors,
        },
        "limitations": [
            "Support labels and sample rates are human judgments, not a truth certificate.",
            "A human reviewer must fill every label before rates are interpreted.",
            "No model self-score is used to manufacture a human semantic accuracy rate.",
            "Review miss rate excludes unclear labels and deterministic claims; a zero unsupported denominator stays null.",
            "Delivered-only enriched samples cannot estimate review error on rejected drafts or all research outputs.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sample", type=Path)
    parser.add_argument("--review-key", type=Path, help="Separate decision key used after blind labeling")
    parser.add_argument("--reviewer", help="Name or pseudonym of the human reviewer; never inferred")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/semantic-blind-label-report.json")
    )
    args = parser.parse_args()
    rows, reviewer = read_labels(json.loads(args.sample.read_text(encoding="utf-8")), args.reviewer)
    review_key = json.loads(args.review_key.read_text(encoding="utf-8")) if args.review_key else None
    if review_key is not None and not isinstance(review_key, dict):
        raise SystemExit("review key must be a JSON object")
    report = score(rows, review_key=review_key, reviewer=reviewer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
