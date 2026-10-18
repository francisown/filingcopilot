"""Decode provider JSON representation quirks without repairing claim content."""

import json

from .models import Submission


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
