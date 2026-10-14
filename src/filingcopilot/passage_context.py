"""Explicit source bundles keep a split paragraph's qualifiers available."""

import re


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
