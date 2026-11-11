"""Offline verifier for an exported monitoring case package.

Two distinct guarantees are reported:

- package integrity: the canonical JSON hash matches;
- content checks: bound evidence exists, fact values and arithmetic agree,
  units agree, and quoted passage text occurs in the source evidence.

Neither guarantee is a semantic certification of an inference.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

VALID_STATUSES = {
    "pending_review",
    "approved",
    "edited",
    "dismissed",
    "needs_followup",
}
MARGIN_METRICS = {"gross_margin", "operating_margin", "net_margin"}
KIND_MAP = {
    "fact": "fact",
    "statement_fact": "calculation",
    "passage": "passage",
}


def _normalise_json(value):
    """Canonicalise numbers so a JSON round-trip does not change the hash.

    PowerShell's ConvertTo-Json may turn ``1.0`` into ``1`` and reformat
    whitespace. The integrity value should survive that lossless JSON round-trip.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except (InvalidOperation, ValueError):
            return str(value)
    if isinstance(value, dict):
        return {str(key): _normalise_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise_json(item) for item in value]
    return value


def _canonical(package: dict) -> str:
    payload = {key: value for key, value in package.items() if key != "integrity"}
    return json.dumps(
        _normalise_json(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def package_sha256(package: dict) -> str:
    return hashlib.sha256(_canonical(package).encode("utf-8")).hexdigest()


def attach_integrity(package: dict) -> dict:
    payload = {key: value for key, value in package.items() if key != "integrity"}
    payload["integrity"] = {
        "sha256": package_sha256(payload),
        "scope": "case_reviews_evidence",
        "note": "Consistency of the saved object, not semantic correctness.",
    }
    return payload


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _check_delta(delta: dict, evidence: dict, checks, errors) -> None:
    metric = str(delta.get("metric") or "")
    current_id = str(delta.get("current_evidence_id") or "")
    prior_id = str(delta.get("prior_evidence_id") or "")
    current_value = _decimal(delta.get("current_value"))
    prior_value = _decimal(delta.get("prior_value"))
    change_abs = _decimal(delta.get("change_abs"))
    change_pct = _decimal(delta.get("change_pct"))
    change_pp = _decimal(delta.get("change_pp"))

    checks.append(current_id in evidence)
    if current_id not in evidence:
        errors.append(f"{metric}: current evidence missing ({current_id})")
    if prior_id:
        checks.append(prior_id in evidence)
        if prior_id not in evidence:
            errors.append(f"{metric}: prior evidence missing ({prior_id})")

    current_ev = evidence.get(current_id) or {}
    prior_ev = evidence.get(prior_id) or {}
    if metric not in MARGIN_METRICS:
        checks.append(str(delta.get("unit") or "") == str(current_ev.get("unit") or ""))
        if str(delta.get("unit") or "") != str(current_ev.get("unit") or ""):
            errors.append(f"{metric}: unit does not match current evidence")
        current_source = _decimal(current_ev.get("value"))
        if current_source is not None and current_value is not None:
            checks.append(_close(current_value, current_source, Decimal("0.01")))
            if not _close(current_value, current_source, Decimal("0.01")):
                errors.append(f"{metric}: current value does not match evidence")
        prior_source = _decimal(prior_ev.get("value"))
        if prior_source is not None and prior_value is not None:
            checks.append(_close(prior_value, prior_source, Decimal("0.01")))
            if not _close(prior_value, prior_source, Decimal("0.01")):
                errors.append(f"{metric}: prior value does not match evidence")

    if current_value is not None and prior_value is not None and change_abs is not None:
        expected = current_value - prior_value
        tolerance = max(Decimal("0.01"), abs(expected) * Decimal("0.000001"))
        checks.append(_close(change_abs, expected, tolerance))
        if not _close(change_abs, expected, tolerance):
            errors.append(f"{metric}: change_abs does not equal current - prior")
    if (
        current_value is not None
        and prior_value is not None
        and prior_value != 0
        and change_pct is not None
        and metric not in MARGIN_METRICS
    ):
        expected = (current_value - prior_value) / abs(prior_value) * 100
        checks.append(_close(change_pct, expected, Decimal("0.05")))
        if not _close(change_pct, expected, Decimal("0.05")):
            errors.append(f"{metric}: change_pct does not match current/prior")
    if current_value is not None and prior_value is not None and change_pp is not None:
        expected = current_value - prior_value
        checks.append(_close(change_pp, expected, Decimal("0.05")))
        if not _close(change_pp, expected, Decimal("0.05")):
            errors.append(f"{metric}: change_pp does not match current - prior")


def _check_source(source: dict, evidence: dict, checks, errors) -> None:
    identifier = str(source.get("id") or "")
    kind = str(source.get("kind") or "")
    checks.append(identifier in evidence)
    if identifier not in evidence:
        errors.append(f"source {identifier}: evidence missing")
        return
    item = evidence[identifier]
    checks.append(KIND_MAP.get(kind, kind) == KIND_MAP.get(str(item.get("kind") or ""), str(item.get("kind") or "")))
    if KIND_MAP.get(kind, kind) != KIND_MAP.get(str(item.get("kind") or ""), str(item.get("kind") or "")):
        errors.append(f"source {identifier}: kind mismatch")
    source_filing = str(source.get("filing_id") or "")
    evidence_filing = str(item.get("filing_id") or (item.get("filing") or {}).get("id") or "")
    if source_filing and evidence_filing:
        checks.append(source_filing == evidence_filing)
        if source_filing != evidence_filing:
            errors.append(f"source {identifier}: filing mismatch")
    quote = _normalise(source.get("quote") or "")
    expected_hash = str(source.get("quote_hash") or "")
    if expected_hash:
        actual_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        checks.append(actual_hash == expected_hash)
        if actual_hash != expected_hash:
            errors.append(f"source {identifier}: quote hash does not match")
    if kind == "passage":
        text = _normalise(item.get("text") or "")
        checks.append(bool(quote) and quote.rstrip("…") in text)
        if not quote or quote.rstrip("…") not in text:
            errors.append(f"source {identifier}: quote not found in passage")
        raw_text = str(item.get("text") or "")
        start = source.get("char_start")
        end = source.get("char_end")
        if start is not None or end is not None:
            valid_span = (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(raw_text)
            )
            checks.append(valid_span)
            if not valid_span:
                errors.append(f"source {identifier}: invalid char span")
            else:
                span = _normalise(raw_text[start:end])
                checks.append(
                    span == quote
                    or quote.startswith(span)
                    or span.startswith(quote)
                )
                if not (
                    span == quote
                    or quote.startswith(span)
                    or span.startswith(quote)
                ):
                    errors.append(f"source {identifier}: char span does not match quote")
    else:
        value = str(item.get("value") or "")
        unit = str(item.get("unit") or "")
        checks.append(bool(quote) and (not value or value in quote) and (not unit or unit in quote))
        if not quote or (value and value not in quote) or (unit and unit not in quote):
            errors.append(f"source {identifier}: numeric quote does not match evidence")


def verify_case_package(package: dict) -> dict:
    errors: list[str] = []
    checks: list[bool] = []

    def check(condition: bool, message: str) -> None:
        checks.append(bool(condition))
        if not condition:
            errors.append(message)

    check(isinstance(package, dict), "Package must be a JSON object")
    if not isinstance(package, dict):
        return {
            "status": "invalid",
            "content_status": "invalid",
            "checks_passed": 0,
            "checks_failed": 1,
            "errors": errors,
        }
    check(package.get("schema") == "monitor-case-v1", "Unexpected package schema")
    integrity = package.get("integrity") or {}
    expected = str(integrity.get("sha256") or "")
    actual = package_sha256(package)
    check(bool(expected) and expected == actual, "Package SHA256 does not match")

    case = package.get("case") or {}
    check(bool(case.get("id")), "Case id is missing")
    check(bool(case.get("ticker")), "Case ticker is missing")
    check(case.get("status") in VALID_STATUSES, "Case status is invalid")
    brief = case.get("brief") or {}
    check(bool(brief.get("headline")), "Brief headline is missing")
    check(bool(brief.get("sources")), "Brief has no sources")

    evidence = package.get("evidence") or {}
    check(isinstance(evidence, dict), "Evidence must be an object")

    source_ids = {
        str(item.get("id"))
        for item in brief.get("sources") or []
        if item.get("id")
    }
    missing_sources = sorted(source_ids - set(evidence))
    check(not missing_sources, f"Missing evidence for sources: {missing_sources[:5]}")

    event_ids: set[str] = set()
    for event in case.get("events") or []:
        event_ids.update(str(item) for item in event.get("evidence_ids") or [] if item)
    missing_event_evidence = sorted(event_ids - set(evidence))
    check(
        not missing_event_evidence,
        f"Missing evidence for events: {missing_event_evidence[:5]}",
    )

    def delta_key(item: dict) -> tuple:
        return (
            item.get("metric"),
            item.get("current_evidence_id"),
            item.get("prior_evidence_id"),
        )

    brief_rows = list(brief.get("numbers") or [])
    event_rows = [
        item
        for event in case.get("events") or []
        for item in event.get("facts") or []
    ]
    brief_deltas: dict[tuple, dict] = {}
    event_deltas: dict[tuple, list[dict]] = {}

    # One brief is one document: duplicate metric rows are still an error.
    for index, item in enumerate(brief_rows):
        key = delta_key(item)
        if key in brief_deltas:
            errors.append(f"brief duplicate number row at index {index}: {key[0]}")
        else:
            brief_deltas[key] = item
        _check_delta(item, evidence, checks, errors)

    # Different events may legitimately reuse the same fact (for example
    # operating cash flow in both financial-results and liquidity events).
    # Reject duplicates only inside one event, and require cross-event reuse
    # to carry exactly the same values.
    for event_index, event in enumerate(case.get("events") or []):
        seen_in_event: set[tuple] = set()
        for item in event.get("facts") or []:
            key = delta_key(item)
            if key in seen_in_event:
                errors.append(
                    f"event {event_index} duplicate number row: {key[0]}"
                )
            seen_in_event.add(key)
            event_deltas.setdefault(key, []).append(item)
            _check_delta(item, evidence, checks, errors)

    for key in sorted(set(brief_deltas) & set(event_deltas), key=str):
        brief_item = brief_deltas[key]
        for event_item in event_deltas[key]:
            for field in (
                "current_value",
                "prior_value",
                "change_pct",
                "change_abs",
                "change_pp",
                "unit",
            ):
                checks.append(brief_item.get(field) == event_item.get(field))
                if brief_item.get(field) != event_item.get(field):
                    errors.append(
                        f"{key[0]}: brief {field} does not match event fact"
                    )

    for key, rows in event_deltas.items():
        first = rows[0]
        for other in rows[1:]:
            for field in (
                "current_value",
                "prior_value",
                "change_pct",
                "change_abs",
                "change_pp",
                "unit",
            ):
                checks.append(first.get(field) == other.get(field))
                if first.get(field) != other.get(field):
                    errors.append(
                        f"{key[0]}: cross-event reused fact differs in {field}"
                    )

    for source in brief.get("sources") or []:
        _check_source(source, evidence, checks, errors)

    for risk in brief.get("risks") or []:
        expected_hash = str(risk.get("quote_hash") or "")
        if expected_hash:
            actual_hash = hashlib.sha256(
                str(risk.get("excerpt") or "").encode("utf-8")
            ).hexdigest()
            checks.append(actual_hash == expected_hash)
            if actual_hash != expected_hash:
                errors.append(
                    f"risk {risk.get('section')}: quote hash does not match excerpt"
                )
        start = risk.get("char_start")
        end = risk.get("char_end")
        if start is not None or end is not None:
            section_text = str(risk.get("section_text") or "")
            char_text = str(risk.get("char_text") or "")
            valid_span = (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(section_text)
                and bool(char_text)
                and section_text[start:end] == char_text
            )
            checks.append(valid_span)
            if not valid_span:
                errors.append(
                    f"risk {risk.get('section')}: invalid char span or char text"
                )

    bad_kinds = [
        item.get("id")
        for item in brief.get("sources") or []
        if item.get("kind") not in KIND_MAP
    ]
    check(not bad_kinds, f"Unknown evidence kinds: {bad_kinds[:5]}")

    content_failures = len(errors)
    package_ok = bool(expected) and expected == actual
    return {
        "status": "consistent" if not errors else "invalid",
        "integrity_status": "consistent" if package_ok else "invalid",
        "content_status": "verified" if content_failures == 0 else "failed",
        "checks_passed": sum(1 for item in checks if item),
        "checks_failed": len(errors),
        "errors": errors,
        "case_id": case.get("id"),
        "ticker": case.get("ticker"),
        "sources": len(source_ids),
        "events": len(case.get("events") or []),
        "numbers_checked": len(brief_rows) + len(event_rows),
        "integrity": "sha256" if expected else "missing",
        "scope": "package integrity, arithmetic, units and quote binding; not semantic correctness",
    }
