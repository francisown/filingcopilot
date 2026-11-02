"""Per-run recall index for the author model context.

The evidence ledger remains the authoritative, complete store used by
verification. This module only decides what the author model sees: a stable-ID
pointer and a short snippet for observations already delivered, plus an
on-demand full read when the model needs the exact object again.

This is deliberately in-process and per-run. Durable cross-restart recall
belongs with resumable execution, not with the verification layer.
"""

from __future__ import annotations

import json
from copy import deepcopy

SNIPPET_CHARS = 240
# Compaction is conservative: only long conversations pay for it, and the most
# recent tool results always stay inline.
COMPACT_TOTAL_CHARS = 60_000
COMPACT_MIN_MESSAGE_CHARS = 1_200
KEEP_RECENT_TOOL_MESSAGES = 6
PREVIEW_LIMIT = 6
SUMMARY_VALUE_CHARS = 500
COMPACT_NOTE = (
    "Earlier tool output was compacted to save context. Call read_recall with "
    "any listed id to retrieve the full observed object."
)


def _text(value: dict) -> str:
    for key in ("text", "statement"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    parts = []
    for key in ("metric", "operation", "label", "concept", "kind", "form"):
        item = value.get(key)
        if item not in (None, ""):
            parts.append(str(item))
    for key in ("value", "result", "unit", "period_end", "fiscal_year"):
        item = value.get(key)
        if item not in (None, ""):
            parts.append(str(item))
    if parts:
        return " ".join(parts)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _snippet(value: dict, limit: int = SNIPPET_CHARS) -> str:
    text = " ".join(_text(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _kind(value: dict) -> str:
    declared = value.get("kind")
    if isinstance(declared, str) and declared:
        return declared
    if "text" in value:
        return "passage"
    if "snapshot" in value:
        return "market"
    if "operation" in value:
        return "calculation"
    if "metric" in value or "value" in value:
        return "fact"
    if "form" in value:
        return "filing"
    return "object"


def _indexable(value: dict) -> bool:
    ref = value.get("id")
    if not isinstance(ref, str) or not ref:
        return False
    return any(
        key in value
        for key in (
            "text",
            "metric",
            "value",
            "operation",
            "snapshot",
            "statement",
            "form",
            "kind",
            "result",
        )
    )


def _rendered(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RecallIndex:
    """Stable-ID index of the objects already delivered to the author model."""

    def __init__(self, *, snippet_chars: int = SNIPPET_CHARS):
        self.snippet_chars = snippet_chars
        self.entries: dict[str, dict] = {}

    def index(self, value, *, pin: bool = False) -> None:
        self._walk(value, pin=pin, seen=set())

    def _walk(self, value, *, pin: bool, seen: set[int]) -> None:
        if isinstance(value, list):
            for item in value:
                self._walk(item, pin=pin, seen=seen)
            return
        if not isinstance(value, dict):
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        if _indexable(value):
            ref = str(value["id"])
            existing = self.entries.get(ref)
            if existing is None:
                self.entries[ref] = {
                    "id": ref,
                    "kind": _kind(value),
                    "snippet": _snippet(value, self.snippet_chars),
                    "pinned": bool(pin),
                    "payload": deepcopy(value),
                }
            else:
                existing["pinned"] = existing["pinned"] or bool(pin)
                # A later compact projection must never replace the richest
                # observed object for the same stable ID.
                if len(_rendered(value)) > len(_rendered(existing["payload"])):
                    existing["payload"] = deepcopy(value)
                    existing["kind"] = _kind(value)
                    existing["snippet"] = _snippet(value, self.snippet_chars)
        for item in value.values():
            self._walk(item, pin=pin, seen=seen)

    def entry(self, ref: str) -> dict | None:
        return self.entries.get(str(ref))

    def read(self, ref: str) -> dict | None:
        entry = self.entry(ref)
        return deepcopy(entry["payload"]) if entry else None

    def pointers(self, refs: list[str]) -> list[dict]:
        preview = []
        for ref in refs:
            entry = self.entries.get(ref)
            if entry is None:
                continue
            pointer = {"id": ref, "kind": entry["kind"], "snippet": entry["snippet"]}
            payload = entry["payload"]
            for key in ("ticker", "section", "page", "filing_id", "period_end"):
                if payload.get(key) not in (None, ""):
                    pointer[key] = payload[key]
            preview.append(pointer)
            if len(preview) >= PREVIEW_LIMIT:
                break
        return preview

    def _collect_ids(self, value, refs: dict[str, None], seen: set[int]) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect_ids(item, refs, seen)
            return
        if not isinstance(value, dict):
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        ref = value.get("id")
        if isinstance(ref, str) and ref in self.entries:
            refs[ref] = None
        for item in value.values():
            self._collect_ids(item, refs, seen)

    @staticmethod
    def _summary(value) -> dict:
        if not isinstance(value, dict):
            return {}
        summary = {}
        for key, item in value.items():
            if key in {"hits", "rows", "evidence", "continuations", "adjacent_passages", "snapshot"}:
                continue
            if len(_rendered(item)) <= SUMMARY_VALUE_CHARS:
                summary[key] = item
        return summary

    def compact(self, messages: list[dict]) -> list[dict]:
        """Replace old, large tool messages with a pointer-and-snippet note.

        The original ``state["messages"]`` stays intact for trace and review.
        Only the model-facing copy is compacted, and only when the accumulated
        tool output is large enough that the saving justifies losing inline
        text. The newest tool results always remain complete.
        """
        tool_indexes = [
            index for index, message in enumerate(messages) if message.get("role") == "tool"
        ]
        if len(tool_indexes) <= KEEP_RECENT_TOOL_MESSAGES:
            return messages
        total = sum(len(str(messages[index].get("content") or "")) for index in tool_indexes)
        if total <= COMPACT_TOTAL_CHARS:
            return messages
        keep = set(tool_indexes[-KEEP_RECENT_TOOL_MESSAGES:])
        compacted = list(messages)
        for index in tool_indexes:
            if index in keep:
                continue
            content = compacted[index].get("content")
            if not isinstance(content, str) or len(content) < COMPACT_MIN_MESSAGE_CHARS:
                continue
            try:
                value = json.loads(content)
            except (TypeError, ValueError):
                continue
            refs: dict[str, None] = {}
            self._collect_ids(value, refs, set())
            if not refs:
                continue
            ordered = list(refs)
            replacement = {
                "recall": {
                    "ids": ordered,
                    "preview": self.pointers(ordered),
                },
                "note": COMPACT_NOTE,
            }
            summary = self._summary(value)
            if summary:
                replacement["summary"] = summary
            compacted[index] = {
                **compacted[index],
                "content": json.dumps(replacement, ensure_ascii=False, separators=(",", ":")),
            }
        return compacted
