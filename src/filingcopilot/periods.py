"""Reporting periods are classified from bound source dates, never calendar quarters."""

from datetime import date


def period_kind(start: date | None, end: date, form: str) -> str | None:
    if form in ("10-K", "10-K/A", "20-F", "20-F/A"):
        return "annual" if start is None or 330 <= (end - start).days <= 400 else None
    if form not in ("10-Q", "10-Q/A") or start is None:
        return None
    days = (end - start).days
    if 70 <= days <= 105:
        return "quarter"
    if 150 <= days <= 210:
        return "ytd6"
    if 240 <= days <= 300:
        return "ytd9"
    return None


def evidence_period(item: dict) -> str | None:
    return period_kind(
        date.fromisoformat(item["start"]) if item.get("start") else None,
        date.fromisoformat(item["end"]),
        item["filing"]["form"],
    )


def comparable_quarters(left: dict, right: dict) -> bool:
    kind = evidence_period(left)
    return bool(
        kind in ("quarter", "ytd6", "ytd9")
        and kind == evidence_period(right)
        and all(
            357 <= (date.fromisoformat(left[k]) - date.fromisoformat(right[k])).days <= 373
            for k in ("start", "end")
        )
        and abs(
            (date.fromisoformat(left["end"]) - date.fromisoformat(left["start"])).days
            - (date.fromisoformat(right["end"]) - date.fromisoformat(right["start"])).days
        )
        <= 7
    )


def fact_inputs(item: dict) -> list[dict]:
    if item["kind"] == "fact":
        return [item]
    if item["kind"] == "calculation":
        return [fact for child in item["inputs"] for fact in fact_inputs(child)]
    return []


def period_label(item: dict) -> str:
    from .ttm import trailing_window

    window = trailing_window(item)
    if window:
        start, end = window
        return f"TTM（{start} 至 {end}）"
    facts = fact_inputs(item)
    base = facts[0] if facts else item
    if base and base.get("filing", {}).get("form") in ("10-Q", "10-Q/A"):
        kind = evidence_period(base)
        label = "单季度" if kind == "quarter" else "年内累计"
        return f"{label}（{base.get('start')} 至 {base['end']}）"
    return f"FY{item['fiscal_year']}"
