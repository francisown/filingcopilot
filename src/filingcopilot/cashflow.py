"""Cash-flow context with exact periods, explicit definitions and immutable inputs."""

import re
from datetime import date

from .capex import capex_basis
from .periods import comparable_quarters, evidence_period, fact_inputs

DEFINITION_NOTE = "自由现金流由本项目按所列公式计算，不是统一的会计准则指标，也不自动等同公司报告口径。现金 PP&E 购置不包括非现金融资租赁取得资产；本公式未另扣融资租赁本金、收购或其他投资。"
NATIVE_DEFINITION_NOTE = "年度数字来自所引 SEC 20-F（IASB 发布的 IFRS 口径），统一使用新台币；不混合台湾年度报告或公告版本。此处现金 PP&E 购置为现金支付毛额，未抵减资产出售收款或政府补助；自由现金流不是公司另行定义的净资本开支口径。最新查询仅指最新已披露完整年度，不能当作最新季度或 TTM。"


def is_cashflow_question(question: str) -> bool:
    return bool(
        re.search(
            r"自由现金流|(?<![A-Za-z0-9_])FCF(?![A-Za-z0-9_])|free cash flow|"
            r"净(?:资本开支|购置支出)|资本开支.{0,8}(?:净额|毛额)|"
            r"PP&E\s*购置(?:净额|毛额)|"
            r"net (?:cash )?(?:capex|capital expenditures|PP&E purchases)",
            question,
            re.I,
        )
    )


def cashflow_scope(question: str):
    from .ttm import is_ttm_question

    if is_ttm_question(question):
        if re.search(r"(?<!\d)20\d{2}(?!\d)", question):
            raise ValueError(
                "TTM 按知悉日期选择最新已披露期间；指定历史窗口请调整知悉日期，不与 FY 年度混用。"
            )
        return "ttm", None
    years = sorted({int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)})
    if len(years) > 1:
        raise ValueError("请一次指定一个自由现金流年度；同比工具会自动查同口径上年。")
    period = (
        "annual"
        if (years or re.search(r"年报|年度|全年|annual|\bFY\b", question, re.I))
        and not re.search(r"累计|半年|季度|单季|\bYTD\b|\bquarter\w*\b", question, re.I)
        else "ytd"
        if re.search(r"累计|半年|\bYTD\b", question, re.I)
        else "quarter"
        if re.search(r"季度|单季|\bquarter\w*\b", question, re.I)
        else "latest"
    )
    return period, years[0] if years else None


def cashflow_explanation(question: str):
    return bool(
        re.search(r"为什么|为何|原因|风险|解释|驱动|来源|质量|用于|用途|投向|why|risk|explain|driver|quality|used for|uses of|purpose|how (?:does|did|is|are|can)", question, re.I)
    )


def native_definition_note(profile):
    if profile.currency == 'TWD' and profile.taxonomy == 'ifrs-full':
        return NATIVE_DEFINITION_NOTE
    standard = 'US-GAAP' if profile.taxonomy == 'us-gaap' else 'IFRS'
    return (f'年度数字来自所引 SEC 20-F（{standard}），申报币种为 {profile.currency}；'
            '不混合其他年度报告或公告版本。现金 PP&E 购置为现金支付毛额，未抵减资产出售收款或政府补助；'
            '自由现金流不是公司另行定义的净资本开支口径。最新查询仅指最新已披露完整年度，不能当作最新季度或 TTM。')


def cashflow_overview(tools, ticker: str, period: str = "latest", fiscal_year: int | None = None):
    ticker = ticker.upper()
    profile = tools.domain.financial_profiles.get(ticker)
    if profile and period in ("quarter", "ytd", "ttm"):
        return {
            "found": False,
            "ticker": ticker,
            "reason": "当前已核验 20-F 年度现金流；6-K 季度现金流尚未接入，不能以年报替代季度、累计或 TTM。",
            "warnings": [],
        }
    if period == "ttm":
        from .ttm import cashflow_ttm

        if fiscal_year is not None:
            raise ValueError("TTM uses as_of rather than a fiscal_year selector")
        return cashflow_ttm(tools, ticker.upper())
    if period not in ("latest", "annual", "quarter", "ytd"):
        raise ValueError("Cash-flow period must be latest, annual, quarter or ytd")
    if tools.fact_loader:
        tools.fact_loader(ticker)
    filings = tools.corpus.catalog(tools.as_of, ticker)
    forms = (
        tools.domain.annual_forms(ticker)
        if period == "annual" or fiscal_year or profile
        else ("10-Q", "10-Q/A")
        if period in ("quarter", "ytd")
        else ("10-K", "10-K/A", "10-Q", "10-Q/A")
    )

    filings = [
        f
        for f in filings
        if f["form"] in forms and (fiscal_year is None or int(f["period_end"][:4]) == fiscal_year)
    ]
    if not filings:
        return {
            "found": False,
            "ticker": ticker,
            "reason": "知悉日期前没有可用现金流申报；请先准备 SEC 报告或调整知悉日期。",
            "warnings": [],
        }
    end = max(f["period_end"] for f in filings)
    filing = next(f for f in filings if f["period_end"] == end)
    annual = filing["form"] in tools.domain.annual_forms(ticker)
    warnings = []
    rows = []
    for metric in ("operating_cash_flow", "cash_capex", "ppe_sale_incentive_proceeds"):
        if metric not in tools.domain.metrics:
            continue
        for year in (int(end[:4]), int(end[:4]) - 1):
            rows.extend(
                tools.corpus.evidence(f.id, tools.as_of)
                for f in tools.corpus.facts(
                    ticker,
                    metric,
                    year,
                    tools.as_of,
                    forms=tools.domain.annual_forms(ticker) if annual else ("10-Q", "10-Q/A"),
                )
                if f.filing_id == filing["id"]
            )
    current = [e for e in rows if e["metric"] == "operating_cash_flow" and e["end"] == end]
    desired = {"annual"} if annual else {"quarter"} if period == "quarter" else {"ytd6", "ytd9"}
    scoped = [e for e in current if evidence_period(e) in desired]
    if not scoped and current and not annual:
        scoped = current
        if period in ("quarter", "ytd"):
            warnings.append(
                "所需单季度或累计现金流未完整披露；仅展示下方明确标注的实际期间，不能把累计数当单季度。"
            )
    if len({(e["start"], e["end"], e["value"]) for e in scoped}) != 1:
        return {
            "found": False,
            "ticker": ticker,
            "reason": "本次报告缺少唯一的所需经营现金流期间；不能以其他期间替代。",
            "warnings": warnings,
        }
    ocf = scoped[0]
    scope = "annual" if annual else "quarter" if evidence_period(ocf) == "quarter" else "ytd"

    def compute(cash):
        purchases = [
            e
            for e in rows
            if e["metric"] == "cash_capex"
            and (e["start"], e["end"]) == (cash["start"], cash["end"])
        ]
        if len({e["value"] for e in purchases}) != 1:
            return None
        inputs = [cash, purchases[0]]
        receipts = [
            e
            for e in rows
            if e["metric"] == "ppe_sale_incentive_proceeds"
            and (e["start"], e["end"]) == (cash["start"], cash["end"])
        ]
        if ticker == "AMZN":
            if len({e["value"] for e in receipts}) == 1:
                inputs.append(receipts[0])
            else:
                warnings.append(
                    "亚马逊披露的净购置口径需出售及激励收款；本期未提取到该调整项，仅计算明确标注的购置总额口径，不把缺值当零。"
                )
        for item in inputs:
            tools.ledger[item["id"]] = item
        try:
            return tools.calculate(
                "free_cash_flow",
                inputs[0]["id"],
                inputs[1]["id"],
                inputs[2]["id"] if len(inputs) == 3 else None,
            )["evidence"]
        except ValueError as exc:
            warnings.append("现金流输入未通过期间、币种或非负购置金额核对：" + str(exc))
            return None

    result = compute(ocf)
    if result is None:
        return {
            "found": False,
            "ticker": ticker,
            "reason": "缺少同一申报和实际期间的现金 PP&E 购置；不能用资产余额、非现金租赁或零替代。可查看对应现金流量表。",
            "filing": filing,
            "warnings": warnings,
        }
    comparisons = [
        e
        for e in rows
        if e["metric"] == "operating_cash_flow"
        and (
            annual
            and int(e["end"][:4]) == int(end[:4]) - 1
            and e["start"] is not None
            or not annual
            and comparable_quarters(ocf, e)
        )
    ]
    previous = (
        compute(comparisons[0])
        if len({(e["start"], e["end"], e["value"]) for e in comparisons}) == 1
        else None
    )
    change = growth = None
    component_growth = []
    if previous is not None:
        try:
            change = tools.calculate("difference", result["id"], previous["id"])["evidence"]
            try:
                growth = tools.calculate("growth", result["id"], previous["id"])["evidence"]
            except ValueError:
                warnings.append(
                    "上年自由现金流为零或负数，百分比同比没有可靠正数基期；已提供变化额。"
                )
        except ValueError as exc:
            warnings.append("自由现金流上年口径不可比：" + str(exc))
        for a, b in zip(result["inputs"], previous["inputs"]):
            if a["metric"] != b["metric"]:
                continue
            try:
                component_growth.append(tools.calculate("growth", a["id"], b["id"])["evidence"])
            except ValueError:
                pass
    else:
        warnings.append("同口径上年现金流或资本开支缺失；本次保留当期结果。")
    observed = [
        *result["inputs"],
        result,
        *([previous] if previous else []),
        *([change] if change else []),
        *([growth] if growth else []),
        *component_growth,
    ]
    basis = capex_basis(tools, result, previous)
    observed.extend(
        tools.ledger[basis[key]]
        for key in ("net_id", "prior_net_id", "net_growth_id")
        if basis[key]
    )
    keys = (
        "id",
        "kind",
        "ticker",
        "label",
        "metric",
        "value",
        "unit",
        "start",
        "end",
        "operation",
        "formula",
        "input_ids",
    )
    return {
        "found": True,
        "ticker": ticker,
        "filing": filing,
        "period_scope": scope,
        "start": ocf["start"],
        "end": end,
        "current_id": result["id"],
        "previous_id": previous["id"] if previous else None,
        "change_id": change["id"] if change else None,
        "growth_id": growth["id"] if growth else None,
        "component_growth_ids": [e["id"] for e in component_growth],
        "definition_note": DEFINITION_NOTE + (native_definition_note(profile) if profile else ""),
        "capex_basis": basis,
        "evidence": [{k: e[k] for k in keys if k in e} for e in observed],
        "warnings": list(dict.fromkeys(warnings)),
    }


def compare_cashflow(tools, operation, left, right):
    if operation not in ("growth", "difference") or any(
        e["operation"] not in ("free_cash_flow", "net_cash_capex") for e in (left, right)
    ):
        raise ValueError("Only like-for-like cash-flow calculations can be compared")
    a, b = fact_inputs(left)[0], fact_inputs(right)[0]
    if (
        left["ticker"] != right["ticker"]
        or left["operation"] != right["operation"]
        or left["unit"] != right["unit"]
        or left["formula"] != right["formula"]
        or left["label"] != right["label"]
        or left["fiscal_year"] != right["fiscal_year"] + 1
        or evidence_period(a) != evidence_period(b)
        or evidence_period(a) == "annual"
        and not 357 <= (date.fromisoformat(a["end"]) - date.fromisoformat(b["end"])).days <= 373
        or evidence_period(a) != "annual"
        and not comparable_quarters(a, b)
    ):
        raise ValueError(
            "Cash-flow comparison requires identical definitions and comparable exact source periods"
        )
