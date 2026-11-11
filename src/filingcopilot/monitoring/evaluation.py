"""Event-level scoring for historical replay and pilot gold sets.

The scorer deliberately keeps precision and recall separate per event type.
A no-event control row is a filing that a human labelled as having no material
event; any predicted event for that filing is a false positive.
"""

from __future__ import annotations

import math


def _wilson(successes: int, total: int, z: float = 1.96) -> list[float | None]:
    if total <= 0:
        return [None, None]
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
        / denominator
    )
    return [
        round(max(0.0, centre - margin), 4),
        round(min(1.0, centre + margin), 4),
    ]


def _key(event: dict) -> tuple:
    return (
        str(event.get("ticker") or "").upper(),
        str(event.get("filing_id") or ""),
        str(event.get("event_type") or ""),
        str(event.get("subtype") or ""),
    )


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "precision_ci": _wilson(tp, tp + fp),
        "recall_ci": _wilson(tp, tp + fn),
    }


def score_events(gold: dict, predicted: dict | list) -> dict:
    gold_events = gold.get("events") or []
    predicted_events = (
        predicted.get("events") if isinstance(predicted, dict) else predicted
    ) or []
    gold_keys = {_key(item) for item in gold_events}
    predicted_keys = {_key(item) for item in predicted_events}
    no_event = {
        (str(item.get("ticker") or "").upper(), str(item.get("filing_id") or ""))
        for item in gold.get("no_event") or []
    }

    types = sorted({key[2] for key in gold_keys | predicted_keys})
    per_type: dict[str, dict] = {}
    for event_type in types:
        gold_subset = {key for key in gold_keys if key[2] == event_type}
        predicted_subset = {key for key in predicted_keys if key[2] == event_type}
        per_type[event_type] = _prf(
            len(gold_subset & predicted_subset),
            len(predicted_subset - gold_subset),
            len(gold_subset - predicted_subset),
        )

    true_positive = gold_keys & predicted_keys
    false_positive = predicted_keys - gold_keys
    false_negative = gold_keys - predicted_keys
    no_event_false_positive = [
        key for key in false_positive if (key[0], key[1]) in no_event
    ]
    no_event_count = len(no_event)
    return {
        "schema": "monitor-event-score-v1",
        "overall": _prf(len(true_positive), len(false_positive), len(false_negative)),
        "per_type": per_type,
        "missing": sorted("|".join(key) for key in false_negative),
        "false_positives": sorted("|".join(key) for key in false_positive),
        "no_event_controls": no_event_count,
        "no_event_false_positives": sorted(
            "|".join(key) for key in no_event_false_positive
        ),
        "no_event_fp_rate": (
            round(len(no_event_false_positive) / no_event_count, 4)
            if no_event_count
            else None
        ),
        "gold_events": len(gold_keys),
        "predicted_events": len(predicted_keys),
    }
