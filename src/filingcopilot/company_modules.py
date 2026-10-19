"""Company-research module contracts: one fixed format per summary section.

Each contract binds a page section to its presentation kind, its research
question, the points and prohibitions used in the model brief, a deterministic
boundary sentence, and the gap text used when a section has no evidence.
The browser reads the contract from the research-page payload; prompts are
built here so the frontend never carries a second copy of the questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModuleKey = Literal[
    "business",
    "operating_change",
    "cashflow",
    "debt",
    "risk",
    "recent_events",
    "valuation",
    "ownership",
    "audit",
]


@dataclass(frozen=True)
class CompanyModule:
    key: str
    title: str
    title_en: str
    kind: Literal["analysis", "factual"]
    question: str
    focus: str
    points: tuple[str, ...]
    prohibitions: tuple[str, ...]
    tools: str
    boundary: str
    empty_gap: str


MODULES: dict[str, CompanyModule] = {
    "business": CompanyModule(
        key="business",
        title="商业模式",
        title_en="Business model",
        kind="analysis",
        question=(
            "What is the company business model: how it makes money, and the disclosed "
            "products, services, segments, customers and markets?"
        ),
        focus="how the company makes money: its disclosed products, services, business "
        "segments, customers and markets",
        points=(
            "disclosed product or service lines and business segments",
            "customer types, channels and markets when disclosed",
            "how revenue is generated (subscription, sales, fees) only where the source states it",
            "disclosed structural changes such as new segments, stated without inferring effects",
        ),
        prohibitions=(
            "do not fill in business lines from industry knowledge, the company name or the ticker",
            "never turn planned activities into operating facts",
            "do not state anything the disclosure does not support",
        ),
        tools="search_passages (the business chapter), get_company_overview",
        boundary=(
            "本段只归纳已披露的业务内容；未披露的分部结构、收入占比与客户集中度不作推断，"
            "未结构化的分部金额另行核验。"
        ),
        empty_gap="未取得业务章节原文；可先“检查最新披露”再重试本节。",
    ),
    "operating_change": CompanyModule(
        key="operating_change",
        title="经营变化",
        title_en="Operating changes",
        kind="analysis",
        question=(
            "What are the latest disclosed operating changes, their drivers, and management's "
            "stated plans for the business?"
        ),
        focus="the latest disclosed operating changes, their drivers and management's stated plans",
        points=(
            "direction of the latest annual operating results as the evidence supports it",
            "the latest quarter or year-to-date change when a recent quarterly filing is available, "
            "kept separate from the annual view",
            "disclosed drivers such as price, volume, acquisitions or one-off items",
            "management's explicitly stated plans or outlook, labelled as plans rather than outcomes",
        ),
        prohibitions=(
            "do not mix annual and quarterly periods in one statement",
            "do not present plans as results already achieved",
            "write 'not found in the cited material' instead of claiming the company did not disclose",
            "do not infer a lasting trend from a single period",
            "avoid vague intensity words such as全面、大幅、显著 or高位 unless a cited calculation or source uses that characterization",
        ),
        tools="get_company_overview, get_quarterly_overview, search_passages",
        boundary=(
            "年度与最新季度/YTD 分列陈述、不得混用；单期变化不能推出持续趋势；已披露计划不等同已实现结果。"
        ),
        empty_gap="未取得经营章节或季报原文；可先“检查最新披露”再重试本节。",
    ),
    "cashflow": CompanyModule(
        key="cashflow",
        title="现金流与资本投入",
        title_en="Cash flow and capital investment",
        kind="analysis",
        question=(
            "How do operating cash flow and capital spending compare with earnings, and what "
            "conditions affect cash quality?"
        ),
        focus="how operating cash flow and capital spending compare with earnings, and what "
        "conditions affect cash quality",
        points=(
            "operating cash flow direction relative to earnings, with disclosed components such as "
            "working-capital movements",
            "capital spending with gross versus net PP&E purchases kept distinct",
            "the operating-cash-flow-minus-gross-capex proxy and why it is not distributable cash",
            "disclosed conditions affecting cash quality such as seasonality, one-off items or "
            "customer receipts",
        ),
        prohibitions=(
            "do not call the proxy free cash flow available for distribution",
            "do not claim cash is abundant or tight beyond the source wording",
            "do not treat investment spending as an investment return",
            "do not equate operating-cash-flow growth with earnings-quality improvement",
        ),
        tools="get_cashflow, search_passages",
        boundary=(
            "经营现金流变化不代表盈利质量变化；“经营现金流减毛资本支出”是代理口径，"
            "不是可分配现金或企业自由现金流。"
        ),
        empty_gap="未取得现金流相关事实与原文；可先“检查最新披露”再重试本节。",
    ),
    "debt": CompanyModule(
        key="debt",
        title="负债与流动性",
        title_en="Debt and liquidity",
        kind="analysis",
        question=(
            "What debt balance, maturity structure, liquidity sources and disclosed covenant "
            "conditions are reported?"
        ),
        focus="reported debt, maturity structure, liquidity sources and disclosed covenant conditions",
        points=(
            "reported debt composition such as borrowings, notes or lease liabilities, on the "
            "disclosed basis",
            "maturity structure by period when a maturity table is disclosed",
            "liquidity sources such as cash and available credit facilities when disclosed",
            "explicitly disclosed covenant or leverage terms",
        ),
        prohibitions=(
            "do not compute net debt, interest coverage or refinancing needs from market data",
            "do not equate gross borrowings with net debt",
            "do not read new issuance itself as liquidity stress or improvement",
        ),
        tools="search_passages (the liabilities and liquidity chapter), get_statement",
        boundary="本段只用披露口径；不推算净债务、利息覆盖或再融资安排，借款毛额不等于净债务。",
        empty_gap="未取得负债与流动性章节原文；可先“检查最新披露”再重试本节。",
    ),
    "risk": CompanyModule(
        key="risk",
        title="风险",
        title_en="Material risks",
        kind="analysis",
        question=(
            "What are the main material risks disclosed in the latest annual report, and which "
            "signals would show they are materializing?"
        ),
        focus="the main material risks disclosed in the latest annual report and signals worth monitoring",
        points=(
            "two or three material risk themes grouped as the source phrases them (operations, "
            "regulation, demand, supply chain, concentration, macro)",
            "the source-stated pathway for each risk",
            "observable signals the disclosure itself provides, if any",
            "keep disclosed risks distinct from events that already happened",
        ),
        prohibitions=(
            "do not rank the largest risk unless the source ranks it",
            "do not assign probabilities",
            "do not present a possible risk as something that already happened",
            "do not claim the cited passages cover every risk in the filing",
        ),
        tools="search_passages (risk factors)",
        boundary="披露风险是可能性而非已发生事件；所引段落不能证明覆盖整份年报的风险清单；不给发生概率。",
        empty_gap="未取得风险因素章节原文；可先“检查最新披露”再重试本节。",
    ),
    "recent_events": CompanyModule(
        key="recent_events",
        title="近期事件",
        title_en="Recent events",
        kind="factual",
        question=(
            "Which official filings and dated public updates are available on or before the "
            "knowledge date, with exact dates and source types?"
        ),
        focus="official filings and dated public updates available on or before the knowledge date",
        points=(
            "for each item: its date and source type (official filing, exchange notice, external "
            "update), with one line on the core action",
            "how the item relates to the latest results release when the source shows it",
            "when nothing is available, state which sources were checked",
        ),
        prohibitions=(
            "do not infer share-price or earnings effects",
            "do not merge different sources into one event",
            "do not treat retrieval time as the publication date",
            "no causal narrative",
        ),
        tools="get_company_updates",
        boundary="官方公告与外部更新分开列示；来源接收时间不等于首次公开时间；摘录不全处以原文为准。",
        empty_gap="知悉日期前未取得官方公告或已接入的外部更新；可稍后重试“检查最新披露”。",
    ),
    "valuation": CompanyModule(
        key="valuation",
        title="估值",
        title_en="Valuation conditions",
        kind="analysis",
        question=(
            "What annual-basis and supplier trailing-basis valuation multiples are observed, and "
            "which assumptions still need testing before assessing price?"
        ),
        focus="observed annual-basis and supplier trailing-basis valuation multiples and the "
        "assumptions still required",
        points=(
            "the annual-earnings-basis multiple and the supplier trailing basis multiple, stated "
            "separately with their bases",
            "the fact that the two bases need separate reconciliation, without explaining the difference",
            "profit-quality questions supported by sources, such as one-off items",
            "which normalisation, benchmarks or assumptions are still required before any price "
            "assessment",
        ),
        prohibitions=(
            "a single multiple cannot prove cheap or expensive",
            "no price target, rating or buy/sell language",
            "do not present the basis difference as a cause",
            "do not state the shares are cheap or expensive",
            "when a typed P/E calculation is supplied, report its observed value and basis instead of replacing it with a generic limitation",
        ),
        tools="get_stock_valuation, get_provider_ttm_valuation, get_stock_market",
        boundary="单一倍数不能证明便宜或昂贵；年度与供应商 TTM 口径需分别核对；本段不产出目标价或买卖意见。",
        empty_gap="未取得同口径 EPS 或匹配报价；请查看历史年度市盈率、同行参照或编辑估值情景后再评估，不以其它口径替代。",
    ),
    "ownership": CompanyModule(
        key="ownership",
        title="上市股份与币种",
        title_en="Listed shares and currencies",
        kind="factual",
        question=(
            "How are the listed share classes, any depositary share ratio and the reporting "
            "versus trading currencies disclosed, and which quotation and earnings pairings are "
            "valid without conversion?"
        ),
        focus="listed share classes, any depositary ratio, and the reporting versus trading currencies",
        points=(
            "share classes and listing venues as disclosed",
            "the depositary-receipt ratio when the source states it",
            "reporting currency versus trading currency",
            "which price and earnings pairings are valid as is, and which would require conversion "
            "the system does not perform",
        ),
        prohibitions=(
            "do not guess depositary ratios or exchange rates",
            "never validate mixing RMB earnings with USD prices",
            "do not convert or annualize without explicit source data",
        ),
        tools="search_passages (share classes and currency disclosures)",
        boundary="按原文配对股份类别与币种；未披露的比例与汇率不推算；跨币种的价格与 EPS 不得直接联算。",
        empty_gap="未取得股份与币种章节原文；比例与汇率不作推算，可稍后重试。",
    ),
    "audit": CompanyModule(
        key="audit",
        title="审计报告原文",
        title_en="Audit report original text",
        kind="factual",
        question=(
            "What does the audit report state about the opinion type, the statements and periods "
            "covered, the auditor and the report date?"
        ),
        focus="what the audit report states about its opinion, coverage, auditor and report date",
        points=(
            "the opinion type exactly as the report words it, and which statements it covers",
            "the periods, auditor name and report date when shown",
            "how the report maps to the latest annual year",
            "key audit matters when the material includes them",
        ),
        prohibitions=(
            "no financial-health judgement",
            "do not read an emphasis paragraph as a qualified opinion, or the reverse",
            "do not merge different years, auditors or report scopes",
        ),
        tools="search_passages (the audit report)",
        boundary=(
            "本段是审计报告原文的归位整理，不是自动审计结论；不同年度、审计师与报告对象不得合并陈述。"
        ),
        empty_gap="未取得审计报告原文；不生成审计解释，可先“检查最新披露”再重试。",
    ),
}


def module_contract(key: str) -> CompanyModule | None:
    return MODULES.get(key)


def statement_char_limit(language: str) -> int:
    """One module statement stays a short paragraph in either output language."""
    return 220 if language != "en" else 300


def module_instruction(key: str, language: str = "zh") -> str:
    """English model brief appended after the shared system prompt.

    The shared system prompt is English for both output languages; only the
    statement length follows the requested output language.
    """
    module = MODULES[key]
    limit = statement_char_limit(language)
    points = "; ".join(module.points)
    prohibitions = "; ".join(module.prohibitions)
    if module.kind == "analysis":
        if key == "valuation":
            numeric_guidance = (
                " For the leading conclusion, state each observed annual-basis or supplier-TTM "
                "P/E multiple when its typed calculation is available, using the exact observed "
                "number and basis. At most two such multiples may appear. Do not invent a range, "
                "target price, forecast, or cheap/expensive judgement; state the exact missing "
                "benchmark or assumption instead."
            )
        elif key in {"operating_change", "cashflow"}:
            numeric_guidance = (
                " Cite the relevant typed fact/calculation/market evidence IDs even when the prose "
                "stays qualitative; the page will render the verified figure and period beside the "
                "conclusion. Do not omit a supported calculation merely because its value is shown "
                "separately."
            )
        else:
            numeric_guidance = ""
        return (
            f"\nCOMPANY-RESEARCH MODULE BRIEF — {module.title_en}: this request writes ONE short "
            "section summary for the company research page, not a stock question. "
            f"Stay strictly inside this scope: {module.focus}. "
            f"Cover, only where evidence exists: {points}. "
            f"Most relevant tools: {module.tools}. "
            "Write a connected short article and deliver one to three kind=analysis claims as paragraphs, each a single "
            f"focused statement of at most {limit} characters (output language): first the leading "
            "conclusion, then at most two supporting points. Do not use bullet-like fragments, generic "
            "introductions or repeated headings. Statements stay qualitative: no amounts, percentages, ratios or numeric "
            "magnitudes in the prose; cite observed evidence IDs for every premise, including "
            "conditional sentences; keep annual, quarter/YTD and trailing periods explicitly "
            "separate. Do not pad with a general company overview or repeat another section's "
            "wording. Lead with a concrete, source-supported conclusion when one exists; do not use "
            "the fixed boundary sentence or a generic request for more data as the main conclusion. "
            "Omit any point without evidence and disclose the specific gap (what is "
            "missing and how to obtain it) in the output language. Never assert that a filing or "
            "notice omits a fact based only on the retrieved excerpts; qualify it as \"not stated "
            "in the cited excerpts\" or leave it to the gap list. "
            f"Prohibitions: {prohibitions}." + numeric_guidance
        )
    return (
        f"\nCOMPANY-RESEARCH MODULE BRIEF — {module.title_en} (factual restatement): this request "
        "writes ONE short section summary for the company research page. "
        f"Restate only what the observed sources state about: {module.focus}. "
        f"Cover, only where the sources state it: {points}. "
        f"Most relevant tools: {module.tools}. "
        "Write a connected short article and deliver one to three kind=narrative claims as paragraphs, each at most "
        f"{limit} characters (output language), each citing its exact evidence IDs; do not use bullet-like fragments or repeated headings; keep original "
        "periods and scope qualifiers. Statements stay qualitative: no amounts, percentages, "
        "ratios or numeric magnitudes, and no operating judgement or interpretation. "
        "Omit any point the sources do not state and disclose the specific gap (what is missing "
        "and how to obtain it) in the output language. Never assert that a filing or notice "
        "omits a fact based only on the retrieved excerpts; qualify it as \"not stated in the "
        "cited excerpts\" or leave it to the gap list. "
        f"Prohibitions: {prohibitions}."
    )
