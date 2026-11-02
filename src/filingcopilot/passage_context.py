"""Explicit source bundles keep a split paragraph's qualifiers available."""

import re


def adjacent_members(item: dict, candidates: list[dict]) -> list[dict]:
    """Immediate narrative neighbors, with original source and section bounds."""
    def position(value):
        match = re.fullmatch(r"narrative block (\d+), chunk (\d+)(?:/\d+)?; exact text retained", value.get("locator", ""))
        return tuple(map(int, match.groups())) if match else None

    origin = position(item)
    if origin is None or not item.get("section"):
        return []
    ordered = sorted(
        [(position(c), c) for c in candidates if c.get("filing_id") == item.get("filing_id") and position(c) is not None],
        key=lambda pair: (pair[0], pair[1]["id"]),
    )
    index = next((i for i, (_, c) in enumerate(ordered) if c["id"] == item["id"]), None)
    if index is None:
        return []
    result = []
    for neighbor in (index + 1, index - 1):
        if 0 <= neighbor < len(ordered):
            pos, value = ordered[neighbor]
            if value.get("section") == item["section"] and abs(pos[0] - origin[0]) <= 1:
                result.append(value)
    return result


def context_part(item: dict) -> tuple[str, int, int, int] | None:
    match = re.fullmatch(
        r"narrative block (\d+), chunk (\d+)/(\d+); exact text retained",
        item.get("locator", ""),
    )
    if not match:
        return None
    block, index, count = map(int, match.groups())
    if count < 1 or not 0 <= index < count:
        return None
    return item["filing_id"], block, index, count


def context_members(item: dict, candidates: list[dict]) -> list[dict]:
    part = context_part(item)
    if part is None:
        return [item]
    filing, block, _, count = part
    members = [
        candidate
        for candidate in candidates
        if (other := context_part(candidate))
        and (other[0], other[1], other[3]) == (filing, block, count)
    ]
    return sorted(members, key=lambda e: context_part(e)[2])
