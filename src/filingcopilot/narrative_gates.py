"""Independent deterministic narrative gates.

The gate takes a claim statement plus the evidence ledger and returns reasons,
without calling an LLM. It is deliberately a small seam: verification may add
semantic review afterwards, but numeric, period and direction mistakes are
blocked before a model can approve them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .claim_periods import period_comparison_conflict
from .polarity import causal_period_conflict, contradiction, investment_cash_conflict


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reasons: tuple[str, ...] = ()


_FINANCIAL_NUMBER = re.compile(
    r"(?:\d[\d,.]*\s*(?:%|％|[万亿]?美元|USD|dollars|百分)|"
    r"[零〇一二两三四五六七八九十百千万亿兆壹贰叁肆伍陆柒捌玖拾佰仟萬億半]+\s*"
    r"(?:美元|港元|澳元|欧元|日元|加元|新加坡元|人民币|英镑|元|倍|成|%|％|百分点|基点))",
    re.I,
)


def gate_narrative_claim(claim: dict, evidence: list[dict], domain, requested_years: set[int] | None = None) -> GateResult:
    """Return deterministic gate results for a narrative/analysis claim."""
    statement = str(claim.get("statement", ""))
    kind = claim.get("kind", "narrative")
    reasons: list[str] = []
    if not statement:
        reasons.append("叙述断言必须有文字。")
    qualitative = statement.replace("单一倍数", "").replace("一成不变", "")
    if kind == "analysis" and _FINANCIAL_NUMBER.search(qualitative):
        reasons.append("analysis 只能定性；金额和百分比必须绑定 typed fact/calculation。")
    passages = [item.get("text", "") for item in evidence if item.get("kind") == "passage"]
    for conflict in (
        investment_cash_conflict(statement, passages),
        causal_period_conflict(statement, passages, domain),
        contradiction(statement, passages, domain, requested_years or set()),
        period_comparison_conflict(statement, evidence, domain),
    ):
        if conflict and conflict not in reasons:
            reasons.append(conflict)
    return GateResult(allowed=not reasons, reasons=tuple(reasons))
