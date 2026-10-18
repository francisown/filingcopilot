"""Export a small, status-blind sample for human semantic labeling.

The exported rows omit expected behavior, model status, and quality metrics.
They retain the question, answer text, and cited evidence IDs so a reviewer can
label support independently.  This script never fills labels automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from filingcopilot.corpus import Corpus

REVIEW_MARKERS = {"semantic_review", "inference_review"}
REVIEW_KINDS = {"narrative", "analysis"}
HIDDEN_FIELDS = {"verification", "verdicts", "supported", "review_model", "review_status"}


def claim_fingerprint(row: dict) -> str:
    """Bind the review key to the exact text and evidence given to the reviewer."""
    payload = {key: row.get(key) for key in ("question", "kind", "answer", "evidence_ids", "evidence")}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _without_judgments(value):
    if isinstance(value, dict):
        return {
            key: _without_judgments(item)
            for key, item in value.items()
            if key not in HIDDEN_FIELDS and key not in {"inputs", "input_evidence"}
        }
    if isinstance(value, list):
        return [_without_judgments(item) for item in value]
    return deepcopy(value)


def _independent_model_review(run: dict) -> bool:
    """Require recorded, disjoint model identities; a review marker is insufficient.

    A fallback reviewer may also have authored a draft elsewhere in this run.
    Missing historical trace metadata remains unknown, not an independent pass.
    This checks model identities, not independence of a future human annotator.
    """
    authors = [step for step in run.get("trace", []) if step.get("stage") == "reason"]
    reviews = [step for step in run.get("trace", []) if step.get("stage") == "semantic_review"]
    if not authors or not reviews:
        return False
    if any(not isinstance(step.get("model"), str) or not step["model"].strip()
           for step in [*authors, *reviews]):
        return False
    author_models = {step["model"].strip().casefold() for step in authors}
    return all(step.get("review_independence") not in {"not_independent", "unknown"}
               and step["model"].strip().casefold() not in author_models for step in reviews)


def export_claim_sample(
    rows: list[dict],
    load_run: Callable[[str], dict | None],
    *,
    size: int = 20,
    seed: int = 20261011,
    reviewed_only: bool = False,
) -> tuple[list[dict], dict]:
    """Sample delivered claims; retain review decisions in a separate scorer key.

    The small live baseline contains very few model-reviewed claims. Stratified
    sampling keeps those visible rather than filling the worksheet with empty
    refusals or numeric answers. Rates apply to each sampled stratum only.
    """
    if size < 1:
        raise ValueError("sample size must be positive")
    candidates = []
    exclusions = Counter()
    seen_rows = set()
    for row in sorted(rows, key=lambda item: str(item.get("id", ""))):
        identifier = row.get("id")
        if not identifier or identifier in seen_rows:
            raise ValueError("semantic results need unique nonempty IDs")
        seen_rows.add(identifier)
        if not str(row.get("answer", "")).strip():
            exclusions["empty_answer"] += 1
            continue
        run = load_run(row.get("run_id", "")) if row.get("run_id") else None
        if run is None:
            exclusions["missing_frozen_run"] += 1
            continue
        claims = run.get("answer", [])
        captured = "\n".join(str(claim.get("text", "")) for claim in claims)
        if captured != row["answer"]:
            exclusions["changed_frozen_answer"] += 1
            continue
        ledger = {item["id"]: item for item in run.get("evidence", []) if item.get("id")}
        independent = _independent_model_review(run)
        for position, claim in enumerate(claims):
            if not str(claim.get("text", "")).strip():
                exclusions["empty_claim"] += 1
                continue
            evidence_ids = list(claim.get("evidence_ids", []))
            item = {
                "question": row.get("question", ""),
                "kind": claim.get("kind", ""),
                "answer": claim["text"],
                "evidence_ids": evidence_ids,
                "evidence": [_without_judgments(ledger[eid]) for eid in evidence_ids if eid in ledger],
                "missing_evidence_ids": [eid for eid in evidence_ids if eid not in ledger],
                "label": None,
                "label_note": "reviewer fills support / unsupported / unclear",
            }
            reviewed = (independent and claim.get("kind") in REVIEW_KINDS
                        and claim.get("verification") in REVIEW_MARKERS)
            candidates.append((identifier, position, item, reviewed))
    rng = random.Random(seed)
    reviewed = [candidate for candidate in candidates if candidate[3]]
    other = [candidate for candidate in candidates if not candidate[3]]
    # Split evenly when both strata are large; include the complete small review
    # stratum when scarce and use the remaining slots for deterministic claims.
    review_size = min(len(reviewed), size if reviewed_only else max(1, size // 2))
    other_size = 0 if reviewed_only else min(len(other), size - review_size)
    if not reviewed_only:
        review_size = min(len(reviewed), size - other_size)
    selected = rng.sample(reviewed, review_size) + rng.sample(other, other_size)
    rng.shuffle(selected)
    sample, entries = [], []
    for index, (identifier, position, item, reviewed) in enumerate(selected, 1):
        identity = hashlib.sha256(f"{identifier}:{position}".encode()).hexdigest()[:12]
        item["blind_id"] = f"blind-claim-{index:03d}-{identity}"
        sample.append(item)
        entries.append({
            "blind_id": item["blind_id"],
            "claim_sha256": claim_fingerprint(item),
            "independent_review_supported": True if reviewed else None,
        })
    return sample, {
        "schema": "filingcopilot.semantic-blind-key/v1",
        "seed": seed,
        "sampling": "delivered independently reviewed claims only" if reviewed_only else "stratified delivered claims; independent review enriched",
        "population": {"claims": len(candidates), "independent_reviewed": len([c for c in candidates if c[3]])},
        "sample_size": len(sample),
        "exclusions": dict(exclusions),
        "entries": entries,
        "limitations": [
            "Keep this decision key separate from the human labeling worksheet.",
            "Only delivered claims are sampled; rejected drafts and abstentions are outside this calibration.",
            "Independent model review requires recorded reviewer identities disjoint from every author model; unknown history does not qualify.",
            "Strata are enriched; overall sample support is not a population accuracy estimate.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/semantic-blind-sample.json"))
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--reviewed-only", action="store_true", help="Sample narrative/analysis claims with independent review; needs --home")
    parser.add_argument("--home", type=Path, help="Frozen run corpus for claim-level text and evidence")
    parser.add_argument("--review-key", type=Path, help="Separate decision key; never give this file to the blind reviewer")
    args = parser.parse_args()
    if args.reviewed_only and not args.home:
        parser.error("--reviewed-only requires a frozen run corpus via --home")
    rows = json.loads(args.results.read_text(encoding="utf-8"))
    if args.home:
        if not args.review_key:
            parser.error("claim-level export needs --review-key stored separately")
        if args.output.resolve() == args.review_key.resolve():
            parser.error("worksheet and review key must use different files")
        corpus = Corpus(args.home, readonly=True)
        sample, key = export_claim_sample(rows, corpus.run, size=args.size, reviewed_only=args.reviewed_only)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.review_key.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.review_key.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"exported {len(sample)} claim-level blind rows; decision key stored separately")
        return
    rng = random.Random(20261011)
    selected = rng.sample(rows, min(max(1, args.size), len(rows)))
    sample = []
    for index, row in enumerate(selected, 1):
        sample.append(
            {
                "blind_id": f"blind-{index:03d}-{hashlib.sha256(row['id'].encode()).hexdigest()[:8]}",
                "question": row.get("question", ""),
                "answer": row.get("answer", ""),
                "evidence_ids": row.get("evidence_ids", []),
                "label": None,
                "label_note": "reviewer fills support / unsupported / unclear",
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"exported {len(sample)} blind rows to {args.output}")


if __name__ == "__main__":
    main()
