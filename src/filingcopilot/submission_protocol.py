"""Decode provider JSON representation quirks without repairing claim content."""

import json

from .models import Submission
from .passage_context import context_members, context_part


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate submission field")
        result[key] = value
    return result


def _decode(value):
    if len(value) > 60000:
        raise ValueError("Encoded submission exceeds the size limit")
    return json.loads(value, object_pairs_hook=_unique_object)


def parse_submission(arguments: dict) -> Submission:
    """Accept one encoding layer; validate every decoded field with the same schema.

    Some tool providers encode arrays as JSON strings. Another observed shape
    puts the entire object tail inside `claims`: '[...], "gaps": []}'. Only a
    fully valid JSON object can be recovered; truncated claims are never guessed.
    """
    if not isinstance(arguments, dict):
        raise ValueError("submit_answer requires an object with claims and gaps arrays")
    decoded = dict(arguments)
    value = decoded.get("claims")
    if isinstance(value, str):
        try:
            decoded["claims"] = _decode(value)
        except json.JSONDecodeError:
            if set(decoded) != {"claims"} or not value.lstrip().startswith("["):
                raise ValueError("claims must be a JSON array, not a string; submit a shorter complete object") from None
            try:
                decoded = _decode('{"claims":' + value)
            except json.JSONDecodeError:
                raise ValueError("claims must be a JSON array, not a string; submit a shorter complete object") from None
    if isinstance(decoded.get("gaps"), str):
        decoded["gaps"] = _decode(decoded["gaps"])
    return Submission.model_validate(decoded)


def bind_observed_submission(submission: Submission, ledger: dict[str, dict]) -> Submission:
    """Bind mechanical source metadata without changing any authored assertion.

    The ledger decides the type of an empty, single-result numeric reference.
    Complete paragraph bundles already observed are supplied to review together;
    missing chunks, unrelated neighbors and exact excerpts are never expanded.
    Original provider arguments remain untouched for the execution trace.
    """
    data = submission.model_dump()
    passages = [item for item in ledger.values() if item.get("kind") == "passage"]
    for claim in data["claims"]:
        ids = claim["evidence_ids"]
        if claim["kind"] in {"fact", "calculation"} and not claim["statement"] and len(ids) == 1:
            observed_kind = ledger.get(ids[0], {}).get("kind")
            if observed_kind in {"fact", "calculation"}:
                claim["kind"] = observed_kind
        if claim["kind"] not in {"narrative", "analysis"}:
            continue
        expanded = list(ids)
        for eid in ids:
            item = ledger.get(eid, {})
            part = context_part(item) if item.get("kind") == "passage" else None
            if not part or part[3] <= 1:
                continue
            members = context_members(item, passages)
            if len(members) != part[3] or len({context_part(m)[2] for m in members}) != part[3]:
                continue
            expanded = list(dict.fromkeys([*expanded, *(m["id"] for m in members)]))
        if len(expanded) <= 8:
            claim["evidence_ids"] = expanded
    return Submission.model_validate(data)
