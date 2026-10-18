"""Versioned, reproducible source/period wording for typed annual answers."""

from datetime import date

from .localization import current_language
from .periods import evidence_period


def comparable_annual_periods(left: dict, right: dict) -> bool:
    if not all(item.get("kind") == "fact" and evidence_period(item) == "annual"
               and item.get("start") for item in (left, right)):
        return False
    if any(left[key] != right[key] for key in ("ticker", "metric", "unit")):
        return False
    if left["fiscal_year"] != right["fiscal_year"] + 1:
        return False
    offsets = [(date.fromisoformat(left[k]) - date.fromisoformat(right[k])).days
               for k in ("start", "end")]
    # Calendar years and 52/53-week years; retain actual dates and lengths.
    return all(357 <= offset <= 373 for offset in offsets) and abs(offsets[0] - offsets[1]) <= 7


def annual_detail_v1(item: dict) -> str:
    """Keep this version stable: package audits reproduce its exact wording."""
    english = current_language() == "en"
    if item["kind"] == "fact" and evidence_period(item) == "annual":
        filing = item["filing"]
        if english:
            return (f" Source: {filing['form']} {filing['accession']}, filed {filing['filed']}; "
                    f"filing period end {filing['period_end']}. The figure uses the bound "
                    f"FY{item['fiscal_year']} column, which may precede the filing's cover year.")
        return (f"来源：{filing['form']} {filing['accession']}，申报日期 {filing['filed']}，"
                f"申报封面期间截止日 {filing['period_end']}。金额取自绑定的 FY{item['fiscal_year']} "
                "列，可能是该申报中的往年比较列。")
    if item["kind"] == "calculation" and item.get("operation") == "growth":
        left, right = item["inputs"]
        if not comparable_annual_periods(left, right):
            raise ValueError("Annual period comparison needs aligned consecutive annual source windows")
        windows = []
        for fact in (left, right):
            days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days + 1
            windows.append(f"FY{fact['fiscal_year']}: {fact['start']} — {fact['end']} "
                           + (f"({days} days)" if english else f"（{days} 天）"))
        if english:
            return (" Period basis: " + "; ".join(windows) + ". Both inputs are annual amounts "
                    "for the same company, metric and currency. Growth uses these actual windows; "
                    "it is not normalized for day/week counts and does not establish unchanged accounting policies.")
        return ("期间依据：" + "；".join(windows) + "。两项均为同一公司、指标及币种的年度金额，"
                "可按上述实际期间计算同比；未按天数或周数归一化，也不据此认定会计政策完全一致。")
    raise ValueError("Annual details require an annual fact or supported annual growth calculation")
