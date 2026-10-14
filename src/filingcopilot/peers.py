"""Transparent candidate peers for comparison planning.

The registry is deliberately a candidate mapping, not a claim that two issuers
are economically interchangeable. Only symbols already registered in the local
domain are returned, so a peer comparison can be resolved and audited without
silently adding unsupported companies.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files

from .market import symbol

_REGISTRY = json.loads(files("filingcopilot").joinpath("peer-groups.json").read_text("utf-8"))
REGISTRY_VERSION = _REGISTRY["version"]
GROUPS = tuple(_REGISTRY["groups"])


def is_peer_question(question: str) -> bool:
    """Recognize an explicit peer/comparable request, not generic risk prose."""

    return bool(
        re.search(
            r"同行|同业|可比(?:公司|组)?|竞争对手|同类公司|peer(?:s)?|comparables?|peer.?group",
            question,
            re.I,
        )
        and re.search(
            r"估值|市盈率|P\s*/?\s*E|倍数|valuation|multiple|相比较|compare", question, re.I
        )
    )


def candidate_peers(ticker: str, available: set[str] | list[str], limit: int = 3) -> dict:
    """Choose available candidates and expose omissions and the registry basis."""

    canonical = symbol(ticker)
    available_set = {symbol(value) for value in available}
    matches = [group for group in GROUPS if canonical in group["members"]]
    if not matches:
        return {
            "found": False,
            "ticker": canonical,
            "registry_version": REGISTRY_VERSION,
            "reason": "本地候选同行名录没有该证券；请在公司对比中手动选择证券。",
        }
    # Prefer the group with the most locally available members, preserving the
    # registry order as the deterministic tie-breaker.
    group = max(
        matches, key=lambda item: sum(member in available_set for member in item["members"])
    )
    listed = [
        member for member in group["members"] if member in available_set and member != canonical
    ]
    omitted = [member for member in group["members"] if member not in available_set]
    peers = listed[: max(1, min(limit, 3))]
    selection_reasons = {
        member: (
            "纳入：与目标证券同属冻结候选组，且已在本地公司目录中登记。"
            if member in peers
            else "排除：候选组成员当前不在本地公司目录，或超过本次纳入上限。"
        )
        for member in group["members"]
        if member != canonical
    }
    return {
        "found": bool(peers),
        "ticker": canonical,
        "group_id": group["id"],
        "group_label": group["label"],
        "group_members": list(group["members"]),
        "registry_version": REGISTRY_VERSION,
        "peers": peers,
        "omitted": omitted,
        "selection_reasons": selection_reasons,
        "basis": group["basis"],
        "note": "这是可复核的候选同行集合，不代表正式行业分类、业务完全可比或投资结论；"
        "估值仍按各公司的同币种供应商 TTM EPS 与报价分别计算。",
    }


def validate_peer_selection(result: dict, comparison: dict) -> None:
    """Check the frozen selection, without silently substituting today's registry."""

    ticker = symbol(result["ticker"])
    members = result["group_members"]
    peers, omitted = result["peers"], result["omitted"]
    if (
        result["found"] is not True
        or len(members) != len(set(members))
        or ticker not in members
        or not 1 <= len(peers) <= 3
        or len(peers) != len(set(peers))
        or ticker in peers
        or set(peers) - set(members)
        or set(peers) & set(omitted)
        or set(omitted) - set(members)
        or comparison["tickers"] != [ticker, *peers]
        or not result["registry_version"]
        or not result["basis"]
    ):
        raise ValueError("Candidate peer selection differs from the frozen comparison")
    for member in members:
        if symbol(member) != member:
            raise ValueError("Peer members need canonical listing symbols")
