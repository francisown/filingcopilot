"""Deterministic taxonomy, 8-K item mapping and materiality rules for Monitor v1.

The taxonomy is deliberately data-shaped. Event extraction may change, but the
meaning of an event type, an 8-K item code and a materiality rule must remain
stable enough that a saved event can be replayed and reviewed later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

TAXONOMY_VERSION = "monitor-v1"
EXTRACTOR_VERSION = "monitor-v1"

WATCH_ITEMS: dict[str, str] = {
    "revenue": "收入与增长",
    "margin": "利润率与盈利",
    "cash_flow": "经营现金流",
    "guidance": "管理层指引",
    "liquidity": "流动性与现金",
    "debt": "债务与资本结构",
    "legal": "法律与监管",
    "customer_concentration": "客户集中度",
    "supply_chain": "供应链风险",
    "ma": "并购与公司变化",
    "leadership": "管理层与治理",
    "cyber": "网络安全",
}

DEFAULT_WATCH = (
    "revenue",
    "margin",
    "cash_flow",
    "liquidity",
    "debt",
    "legal",
    "ma",
    "leadership",
)

DEFAULT_FORMS = ("10-K", "10-Q", "8-K")
SUPPORTED_FORMS = ("10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A")
FORM_ROOT = {
    "10-K": "10-K",
    "10-K/A": "10-K",
    "10-Q": "10-Q",
    "10-Q/A": "10-Q",
    "8-K": "8-K",
    "8-K/A": "8-K",
}

EVENT_TYPES: dict[str, str] = {
    "financial_results": "财务结果",
    "liquidity_debt": "流动性与债务",
    "legal_regulatory": "法律与监管",
    "guidance": "管理层指引",
    "corporate_change": "公司变化",
    "other_material": "其他重大事项",
}

WATCH_EVENT_MAP: dict[str, tuple[str, ...]] = {
    "revenue": ("financial_results",),
    "margin": ("financial_results",),
    "cash_flow": ("financial_results", "liquidity_debt"),
    "guidance": ("guidance",),
    "liquidity": ("liquidity_debt",),
    "debt": ("liquidity_debt",),
    "legal": ("legal_regulatory",),
    "cyber": ("legal_regulatory",),
    "customer_concentration": ("financial_results", "legal_regulatory"),
    "supply_chain": ("legal_regulatory", "financial_results"),
    "ma": ("corporate_change", "other_material"),
    "leadership": ("corporate_change",),
}

MATERIALITY_RANK = {"suppressed": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class EventSpec:
    event_type: str
    subtype: str
    label: str
    materiality: str
    tags: tuple[str, ...] = ()


# 8-K item codes are the cheapest high-precision event classifier in the stack.
# Item 9.01 is deliberately absent: exhibits alone are not an event.
EIGHT_K_ITEMS: dict[str, EventSpec] = {
    "1.01": EventSpec("liquidity_debt", "material_agreement", "签订重大协议", "medium", ("8-K-1.01",)),
    "1.02": EventSpec("liquidity_debt", "agreement_termination", "终止重大协议", "medium", ("8-K-1.02",)),
    "1.03": EventSpec("liquidity_debt", "bankruptcy", "破产或接管", "high", ("8-K-1.03",)),
    "1.04": EventSpec("legal_regulatory", "mine_safety", "矿山安全", "medium", ("8-K-1.04",)),
    "1.05": EventSpec("legal_regulatory", "cyber_incident", "网络安全事件", "high", ("8-K-1.05",)),
    "2.01": EventSpec("corporate_change", "acquisition_disposition", "完成收购或处置", "high", ("8-K-2.01",)),
    "2.02": EventSpec("financial_results", "earnings_release", "业绩发布", "high", ("8-K-2.02",)),
    "2.03": EventSpec("liquidity_debt", "direct_obligation", "新增直接债务", "high", ("8-K-2.03",)),
    "2.04": EventSpec("liquidity_debt", "debt_acceleration", "债务加速到期", "high", ("8-K-2.04",)),
    "2.05": EventSpec("corporate_change", "exit_disposal", "退出或处置成本", "medium", ("8-K-2.05",)),
    "2.06": EventSpec("financial_results", "impairment", "重大减值", "high", ("8-K-2.06",)),
    "3.01": EventSpec("corporate_change", "delisting", "退市通知", "high", ("8-K-3.01",)),
    "3.02": EventSpec("other_material", "unregistered_sale", "未注册证券销售", "medium", ("8-K-3.02",)),
    "3.03": EventSpec("other_material", "rights_modification", "证券持有人权利变更", "medium", ("8-K-3.03",)),
    "4.01": EventSpec("legal_regulatory", "auditor_change", "审计师变更", "medium", ("8-K-4.01",)),
    "4.02": EventSpec("financial_results", "restatement", "前期报表不可依赖", "high", ("8-K-4.02",)),
    "5.01": EventSpec("corporate_change", "control_change", "控制权变更", "high", ("8-K-5.01",)),
    "5.02": EventSpec("corporate_change", "leadership_change", "董事或高管变更", "medium", ("8-K-5.02",)),
    "5.03": EventSpec("corporate_change", "bylaw_change", "章程或细则变更", "low", ("8-K-5.03",)),
    "5.07": EventSpec("other_material", "shareholder_vote", "股东表决结果", "low", ("8-K-5.07",)),
    "6.01": EventSpec("other_material", "abs_info", "ABS 信息", "low", ("8-K-6.01",)),
    "6.02": EventSpec("other_material", "abs_credit", "ABS 信用增强", "low", ("8-K-6.02",)),
    "6.03": EventSpec("other_material", "abs_servicer", "ABS 服务商变更", "low", ("8-K-6.03",)),
    "6.04": EventSpec("other_material", "abs_trigger", "ABS 触发事件", "low", ("8-K-6.04",)),
    "6.05": EventSpec("other_material", "abs_securitizer", "ABS 证券化机构", "low", ("8-K-6.05",)),
    "7.01": EventSpec("other_material", "reg_fd", "Regulation FD 披露", "medium", ("8-K-7.01",)),
    "8.01": EventSpec("other_material", "other_event", "其他事件", "medium", ("8-K-8.01",)),
}

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "risk_factors": ("risk factor", "risk factors", "风险因素", "风险"),
    "mda": ("md&a", "management's discussion", "management discussion", "管理层讨论", "经营讨论"),
    "liquidity": ("liquidity", "capital resources", "流动", "资本资源", "债务"),
    "legal": ("legal proceeding", "legal proceedings", "litigation", "法律诉讼", "诉讼", "法律"),
    "guidance": ("guidance", "outlook", "指引", "展望"),
}

SECTION_LABELS = {
    "risk_factors": "风险因素",
    "mda": "管理层讨论与分析",
    "liquidity": "流动性与资本资源",
    "legal": "法律诉讼",
    "guidance": "管理层指引",
}

# Sections that are worth a targeted text comparison. Comparing the whole
# document is intentionally rejected: it produces boilerplate and render noise.
DIFF_SECTIONS = tuple(SECTION_LABELS)


def normalise_form(form: str) -> str:
    value = str(form or "").strip().upper()
    return value if value in SUPPORTED_FORMS else value


def form_root(form: str) -> str:
    return FORM_ROOT.get(normalise_form(form), normalise_form(form))


def is_amendment(form: str) -> bool:
    return normalise_form(form).endswith("/A")


def watch_items(value: Iterable[str] | None) -> list[str]:
    if not value:
        return list(DEFAULT_WATCH)
    result: list[str] = []
    for item in value:
        key = str(item).strip()
        if key in WATCH_ITEMS and key not in result:
            result.append(key)
    return result


def event_types_for_watch(items: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for item in items:
        result.update(WATCH_EVENT_MAP.get(item, ()))
    return result


def parse_8k_items(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = [str(item) for item in raw]
    else:
        values = str(raw).replace(";", ",").split(",")
    result: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in result:
            result.append(item)
    return result


def classify_8k(raw) -> list[EventSpec]:
    specs: list[EventSpec] = []
    seen: set[tuple[str, str]] = set()
    for item in parse_8k_items(raw):
        spec = EIGHT_K_ITEMS.get(item)
        if spec is None:
            continue
        key = (spec.event_type, spec.subtype)
        if key not in seen:
            specs.append(spec)
            seen.add(key)
    return specs


def section_key(section: str) -> str | None:
    value = str(section or "").casefold()
    for key, aliases in SECTION_ALIASES.items():
        if any(alias in value for alias in aliases):
            return key
    return None


def max_materiality(*labels: str) -> str:
    best = "suppressed"
    for label in labels:
        if MATERIALITY_RANK.get(label, 0) > MATERIALITY_RANK.get(best, 0):
            best = label
    return best


def materiality_label(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    if score > 0:
        return "low"
    return "suppressed"


def fact_materiality(deltas: Iterable) -> tuple[str, int, list[str]]:
    """Return (label, score, rule hits) for a set of factual deltas.

    The function accepts objects with ``metric``, ``change_pct``,
    ``change_abs`` and ``change_pp`` attributes so it can be unit-tested without
    constructing a full event.
    """
    score = 0
    hits: list[str] = []
    for delta in deltas:
        metric = str(getattr(delta, "metric", ""))
        pct = getattr(delta, "change_pct", None)
        absolute = getattr(delta, "change_abs", None)
        pp = getattr(delta, "change_pp", None)
        abs_value = abs(float(absolute)) if absolute is not None else 0.0
        pct_value = abs(float(pct)) if pct is not None else 0.0
        if metric in {"revenue", "net_income", "operating_cash_flow", "gross_profit", "operating_income"}:
            # High materiality requires both a meaningful percentage change and
            # a meaningful dollar amount. A 100 -> 110 move is not high just
            # because the percentage is 10%.
            if pct_value >= 5 and abs_value >= 10_000_000:
                score = max(score, 75)
                hits.append(f"{metric}_pct_ge_5")
                hits.append(f"{metric}_abs_ge_10m")
            elif pct_value >= 2.5 and abs_value >= 5_000_000:
                score = max(score, 50)
                hits.append(f"{metric}_pct_ge_2.5")
                hits.append(f"{metric}_abs_ge_5m")
        elif metric in {"cash", "debt", "total_assets"}:
            if pct_value >= 10:
                score = max(score, 75)
                hits.append(f"{metric}_pct_ge_10")
            elif pct_value >= 5:
                score = max(score, 50)
                hits.append(f"{metric}_pct_ge_5")
        elif metric in {
            "gross_margin",
            "operating_margin",
            "net_margin",
            "diluted_eps",
        }:
            if pp is not None and abs(float(pp)) >= 2:
                score = max(score, 75)
                hits.append(f"{metric}_pp_ge_2")
            elif pp is not None and abs(float(pp)) >= 1:
                score = max(score, 50)
                hits.append(f"{metric}_pp_ge_1")
    return materiality_label(score), score, hits


def section_materiality(similarity: float, section: str) -> str:
    # Callers often pass the canonical key ("mda", "risk_factors") rather than
    # the display label. Accept both instead of silently downgrading to low.
    key = section if section in SECTION_LABELS else (section_key(section) or "")
    if key not in {"risk_factors", "mda", "liquidity", "legal"}:
        return "low"
    if similarity < 0.90:
        return "high"
    if similarity < 0.97:
        return "medium"
    return "low"



