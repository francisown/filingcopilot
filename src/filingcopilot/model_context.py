"""Serialize model context without transmitting price series embedded in calculations.

This projection is only for model messages. Tools, the ledger, trace, MCP and
research packages retain the complete evidence, including every trading day.
"""

import json

from .market import MarketEvidence, statistics


def _project(value):
    if isinstance(value, list):
        return [_project(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("kind") == "market" and "snapshot" in value:
        # Validate and compute from the original series, never from rounded prices.
        snapshot = MarketEvidence.model_validate(value).snapshot
        return {
            **{key: _project(item) for key, item in value.items() if key != "snapshot"},
            "snapshot": {key: item for key, item in value["snapshot"].items() if key != "points"},
            "statistics": statistics(snapshot),
            "history_note": "Full daily series remains in the evidence ledger and research package; only code-computed range statistics are supplied here.",
        }
    return {key: _project(item) for key, item in value.items()}


def model_context_json(value) -> str:
    """Return compact JSON while preserving exact values, source text and input IDs."""
    return json.dumps(_project(value), ensure_ascii=False, separators=(",", ":"))
