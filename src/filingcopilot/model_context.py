"""Serialize model context without transmitting price series embedded in calculations.

This projection is only for model messages. Tools, the ledger, trace, MCP and
research packages retain the complete evidence, including every trading day.
"""

import json

from .market import MarketEvidence, statistics

_PASSAGE_KEYS = (
    "id",
    "filing_id",
    "ticker",
    "text",
    "section",
    "source_url",
    "locator",
    "page",
    "table_context",
)


def _project_passage(value: dict, seen_passage_ids: set[str] | None = None) -> dict:
    """Keep the source text and locator, dropping ranking-only bookkeeping."""
    result = {
        key: _project(value[key], seen_passage_ids)
        for key in _PASSAGE_KEYS
        if key in value
    }
    passage_id = value.get("id")
    if seen_passage_ids is not None and passage_id:
        if passage_id in seen_passage_ids:
            # The full text is already present in an earlier tool response.
            # Keep scope identity and ID so the model can cite it without
            # multiplying the prompt by repeated paragraphs.
            return {
                key: result[key]
                for key in ("id", "filing_id", "ticker", "section", "page")
                if key in result
            } | {"reused": True}
        seen_passage_ids.add(str(passage_id))
    return result


def _project_search_result(
    value: dict, seen_passage_ids: set[str] | None = None
) -> dict:
    """Compact a search response at the model boundary.

    The retriever keeps scores, legs and diagnostics in its trace and the
    evidence ledger. The author model only needs the observed passage text,
    IDs, locators and the deterministic slot plan. Sending the ranking
    internals back on every round needlessly increases prompt tokens and makes
    long narrative questions slower without adding evidence.
    """
    projected = {
        key: _project(item, seen_passage_ids)
        for key, item in value.items()
        if key not in {"hits", "continuations"}
    }
    hits = []
    for hit in value.get("hits", []):
        if not isinstance(hit, dict) or not isinstance(hit.get("passage"), dict):
            hits.append(_project(hit, seen_passage_ids))
            continue
        item = {"passage": _project_passage(hit["passage"], seen_passage_ids)}
        if "score" in hit:
            item["score"] = hit["score"]
        if "context_ids" in hit:
            item["context_ids"] = list(hit["context_ids"])
        hits.append(item)
    projected["hits"] = hits
    if "continuations" in value:
        projected["continuations"] = [
            _project_passage(item, seen_passage_ids)
            if isinstance(item, dict)
            else _project(item, seen_passage_ids)
            for item in value.get("continuations", [])
        ]
    return projected


def _project(value, seen_passage_ids: set[str] | None = None):
    if isinstance(value, list):
        return [_project(item, seen_passage_ids) for item in value]
    if not isinstance(value, dict):
        return value
    if "hits" in value and isinstance(value.get("hits"), list):
        return _project_search_result(value, seen_passage_ids)
    # ``get_statement`` keeps complete evidence objects in the investigation
    # ledger, but sending every filing wrapper and source field back on every
    # model round can multiply a single statement into hundreds of thousands
    # of input tokens.  The compact rows already contain the typed values and
    # evidence IDs needed for a submission; the verifier resolves the full
    # objects from the ledger later.
    if "statement" in value and "rows" in value and "evidence" in value:
        rows = list(value.get("rows") or [])
        if len(rows) > 40:
            def row_priority(row):
                text = " ".join(
                    str(row.get(key, ""))
                    for key in ("metric", "label", "concept", "dimension_labels")
                ).casefold()
                keywords = (
                    "revenue", "sales", "gross", "operating", "income", "cost",
                    "expense", "tax", "cash", "profit", "毛利", "营业", "收入", "成本",
                )
                return (
                    -sum(keyword in text for keyword in keywords),
                    bool(row.get("dimensions")),
                    str(row.get("id", "")),
                )
            rows = sorted(rows, key=row_priority)[:40]
        return {
            **{
                key: _project(item, seen_passage_ids)
                for key, item in value.items()
                if key not in {"evidence", "rows"}
            },
            "rows": [_project(item, seen_passage_ids) for item in rows],
            "evidence_ids": [
                item.get("id") for item in rows if item.get("id")
            ],
            "omitted_row_count": max(0, len(value.get("rows", [])) - len(rows)),
        }
    if value.get("kind") == "market" and "snapshot" in value:
        # Validate and compute from the original series, never from rounded prices.
        snapshot = MarketEvidence.model_validate(value).snapshot
        return {
            **{
                key: _project(item, seen_passage_ids)
                for key, item in value.items()
                if key != "snapshot"
            },
            "snapshot": {key: item for key, item in value["snapshot"].items() if key != "points"},
            "statistics": statistics(snapshot),
            "history_note": "Full daily series remains in the evidence ledger and research package; only code-computed range statistics are supplied here.",
        }
    return {key: _project(item, seen_passage_ids) for key, item in value.items()}


def model_context_json(value, *, seen_passage_ids: set[str] | None = None) -> str:
    """Return compact JSON while preserving the established message shape.

    The agent and reviewer system prompts provide the instruction/data
    boundary. Keeping the serialized shape stable is important because
    providers and offline tests consume these objects as structured context.
    """
    return json.dumps(
        _project(value, seen_passage_ids), ensure_ascii=False, separators=(",", ":")
    )
