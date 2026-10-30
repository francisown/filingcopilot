"""Stable, user-facing classification for incomplete research runs.

The old UI exposed only ``partial`` or ``abstained``.  Those states were too
coarse: a caller could not tell whether the run ran out of time, lost a model
or source request, failed independent review, or simply lacked evidence.  This
module keeps the status enum backwards compatible while deriving one explicit
reason from the execution receipt.
"""

from __future__ import annotations


def _value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def classify_status_reason(answer) -> str:
    """Return a stable reason key for a ``ResearchAnswer``-like value.

    The function intentionally uses trace and usage metadata rather than
    guessing from the prose.  It is safe for legacy saved runs that have no
    ``status_reason`` field.
    """

    status = _value(answer, "status")
    review_independence = _value(answer, "review_independence", "not_run")
    gaps = list(_value(answer, "gaps", []) or [])
    trace = list(_value(answer, "trace", []) or [])
    usage = dict(_value(answer, "usage", {}) or {})
    stages = {item.get("stage") for item in trace if isinstance(item, dict)}
    gap_text = " ".join(str(item) for item in gaps).lower()

    if any(
        item.get("kind") == "market_source_failure_policy"
        for item in trace
        if isinstance(item, dict)
    ):
        return "source_unavailable"

    if status == "answered" and review_independence in {"not_independent", "unknown"}:
        return "review_incomplete"
    if status == "answered":
        return "complete"
    if usage.get("budget_status") == "reused_evidence":
        return "reused_evidence"
    if usage.get("budget_status") == "cost_exceeded" or "cost_budget" in stages:
        return "cost_budget_exceeded"
    if usage.get("budget_status") == "cancelled" or "cancelled" in stages:
        return "cancelled"
    if (
        usage.get("budget_status") == "expired"
        or "deadline" in stages
        or "时间预算" in gap_text
        or "研究预算内" in gap_text
        or "round limit" in gap_text
    ):
        return "budget_expired"
    if status == "error" or stages.intersection({"model_error", "source_error", "tool_error"}):
        return "source_or_model_error"
    if "模型请求失败" in gap_text or "model request" in gap_text or "provider" in gap_text:
        return "source_or_model_error"
    if "semantic_review_error" in stages or "部分内容未通过证据核验" in gap_text:
        return "review_incomplete"
    coverage = [
        item for item in trace
        if isinstance(item, dict) and item.get("stage") == "source_coverage"
    ]
    if any(item.get("status") == "not_acquired" for item in coverage):
        return "source_not_acquired"
    if any(item.get("status") == "unavailable" for item in coverage):
        return "source_unavailable"
    if status == "abstained":
        return "no_supported_evidence"
    return "evidence_gap"

