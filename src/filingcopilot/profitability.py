"""Reconcile profitability from exact, source-bound statement amounts.

This is arithmetic context for drafting and review, not management attribution.
The gross-to-operating spread can include other operating items as well as expenses.
"""
import re
from collections import defaultdict
from decimal import Decimal, localcontext

from .periods import evidence_period, fact_inputs


def profitability_context(evidence: list[dict]) -> list[dict]:
    """Return only complete same-filing, same-period, same-currency triples."""
    metrics = {"revenue", "gross_profit", "operating_income"}
    groups = defaultdict(lambda: defaultdict(dict))
    for item in evidence:
        for fact in fact_inputs(item):
            if fact.get("metric") not in metrics or evidence_period(fact) != "annual":
                continue
            key = tuple(fact.get(k) for k in ("ticker", "filing_id", "fiscal_year", "start", "end", "unit"))
            groups[key][fact["metric"]][fact["id"]] = fact
    result = []
    for key, group in sorted(groups.items()):
        if set(group) != metrics or any(len({Decimal(f["value"]) for f in items.values()}) != 1
                                         for items in group.values()):
            continue
        chosen = {m: min(items.values(), key=lambda f: f["id"]) for m, items in group.items()}
        revenue, gross, operating = [Decimal(chosen[m]["value"]) for m in
                                    ("revenue", "gross_profit", "operating_income")]
        if revenue <= 0:
            continue
        with localcontext() as context:
            context.prec = 28
            result.append({
                "ticker": key[0], "filing_id": key[1], "fiscal_year": key[2],
                "start": key[3], "end": key[4], "unit": key[5],
                "gross_margin_percent": str(gross / revenue * 100),
                "operating_margin_percent": str(operating / revenue * 100),
                "gross_to_operating_spread_percent": str((gross - operating) / revenue * 100),
                "inputs": [{k: f[k] for k in ("id", "metric", "value")}
                           for _, f in sorted(chosen.items())],
                "scope_note": "Accounting identity: operating margin = gross margin minus the "
                              "gross-to-operating spread. This residual does not identify its "
                              "line-item composition: label it operating expenses or other operating "
                              "items only when a cited statement establishes that classification. "
                              "Rounded display percentages can conceal "
                              "real changes. This does not establish management's causal attribution.",
            })
    return result


def arithmetic_scope_conflict(statement: str, evidence: list[dict]) -> str | None:
    """Require amount premises for amounts and source premises for classification.

    These are necessary conditions; passing does not establish the comparison
    or accounting identity, which still requires independent semantic review.
    """
    passages = [e.get("text", "") for e in evidence if e.get("kind") == "passage"]
    increment_order = (
        r"(?:利润|收入|费用|成本)(?:增长带来的)?(?:增量|增加额)[^。；]{0,25}(?:超过|大于|低于|小于)"
        r"|\b(?:profit|income|revenue|expense|cost)\s+(?:increment|increase in amount)\b"
        r"[^.;]{0,25}\b(?:exceed\w*|greater|less|larger|smaller)\b"
    )
    if (re.search(increment_order, statement, re.I) and not passages
            and not any(e.get("operation") == "difference" for e in evidence)):
        return ("Growth-rate ordering does not establish absolute amount increments. "
                "Cite comparable amount differences for both sides or omit the increment comparison; "
                "retain the supported rate-to-margin explanation.")
    other_items = r"other\s+operating\s+(?:items|income|expenses)|其他经营(?:项目|收益|费用)|其他营业(?:项目|收益|费用)"
    composition = r"\b(?:includes?|contains?|comprises?)\b|包含|包括|构成"
    conditional = r"\b(?:may|might|could|can|if|whether)\b|可能|是否|如果|未必|不一定"
    if (re.search(other_items, statement, re.I) and re.search(composition, statement, re.I)
            and not re.search(conditional, statement, re.I)
            and not any(re.search(other_items, p, re.I) for p in passages)):
        return ("The arithmetic residual does not establish its line-item composition. "
                "Other operating items require a cited statement classification; do not convert "
                "a generic accounting caution into a company-specific fact.")
    return None
