"""Deterministic retrieval text, independent of evaluation targets."""

import re

from .domain import Domain

_PROMPT_WORDS = frozenset(
    "a an the and or of in on for to from with about where which what does did "
    "is are was were it its say says discuss discusses reported report review "
    "find locate evidence filing filings annual filed please show me explain "
    "according disclosure disclosures how s".split()
)
_ZH_PROMPT = re.compile(
    r"请给出|请从|请|我想查|核对|年报里|年报|申报中|申报|哪些段落说明|"
    r"找出|原文依据|原文证据|原文|是怎样披露的|关于|的表述|表述|披露|怎样|哪些|说明"
)


def retrieval_text(expanded: str, domain: Domain, ticker: str | None, fiscal_year: int | None) -> str:
    """Remove boilerplate and metadata only when the metadata is already scoped.

    No expected passage, section, or gold-set term enters this function. Keep
    unscoped names/years, financial qualifiers and negations in the query.
    """
    value = expanded
    if ticker and ticker in domain.companies:
        company = domain.companies[ticker]
        for name in sorted([ticker, company.name, *company.aliases], key=len, reverse=True):
            if name.isascii():
                value = re.sub(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", " ", value, flags=re.I)
            else:
                value = value.replace(name, " ")
    if fiscal_year is not None:
        value = re.sub(r"\bFY\s*" + str(fiscal_year) + r"\b|\b" + str(fiscal_year) + r"\b", " ", value, flags=re.I)
    value = _ZH_PROMPT.sub(" ", value)
    terms = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+", value)
    kept = [term for term in terms if term.casefold() not in _PROMPT_WORDS]
    return " ".join(dict.fromkeys(kept)) or expanded


def lexical_terms(query: str) -> list[str]:
    """Keep Chinese literals as well as Latin finance terms, with bounded size."""
    return list(dict.fromkeys(re.findall(r"[A-Za-z0-9]{2,}|[\u3400-\u9fff]{2,}", query)))[:40]


_CAUSE_Q = re.compile(
    r"原因|为什么|为何|驱动|因素|解释|变化|\bwhy\b|\bdrivers?\b|\bcauses?\b|\bexplain\b|\bgrowth\b|\bmargin\b",
    re.I,
)
_RISK_Q = re.compile(r"风险|\brisks?\b|监管|\bregulat|集中度|\bconcentrat", re.I)
_NEG_Q = re.compile(
    r"否认|没有重大|无重大|是否明确|是否披露没有|\bdeny\w*\b|\bno material\b", re.I
)

_DECOMPOSITIONS = {
    "cause": [
        "net revenue net sales increased decreased primarily due to",
        "segment revenue net sales driver demand products",
        "operating income gross margin cost of sales expense change",
    ],
    "risk": [
        "risk factors regulatory competition export controls",
        "customer concentration major customers revenue",
        "supply chain suppliers operations interruption",
    ],
    "negation": [
        "no material impact not materially did not affect",
        "supply chain disruption demand decline affected results",
        "management discussion demand supply chain impact",
    ],
}


def question_shape(question: str) -> str:
    """Which family of research a question requires: cause, risk, negation, other."""
    text = str(question)
    if _NEG_Q.search(text):
        return "negation"
    if _RISK_Q.search(text):
        return "risk"
    if _CAUSE_Q.search(text):
        return "cause"
    return "other"


def evidence_slot_plan(question: str) -> list[dict[str, object]]:
    """Describe the evidence parts a question normally needs.

    This is a planning aid, not a correctness judgment.  Keeping the plan
    deterministic lets the agent, the retriever and the evaluator use the same
    vocabulary for missing evidence without asking a model to invent a checklist.
    The slots are intentionally broad so they generalize across issuers and
    filing years.
    """
    text = str(question)
    shape = question_shape(text)
    years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    slots: list[dict[str, object]] = []
    if years:
        slots.append({"id": "period", "required": True, "terms": [str(max(years))]})
    if shape == "cause":
        slots.extend(
            [
                {"id": "change", "required": True, "terms": ["increase", "decrease", "change"]},
                {"id": "driver", "required": True, "terms": ["because", "due to", "driven by", "primarily"]},
            ]
        )
    elif shape == "risk":
        slots.append({"id": "risk_disclosure", "required": True, "terms": ["risk", "adverse", "could"]})
        lowered = text.casefold()
        if re.search(r"集中度|客户|concentrat|major customer", lowered, re.I):
            slots.append({"id": "customer_concentration", "required": True, "terms": ["customer", "concentration", "revenue"]})
        if re.search(r"监管|regulat|出口|export|competition|竞争", lowered, re.I):
            slots.append({"id": "regulatory_or_competition", "required": True, "terms": ["regulatory", "competition", "export"]})
        if re.search(r"需求|demand|宏观|macro", lowered, re.I):
            slots.append({"id": "demand", "required": True, "terms": ["demand", "customer", "economic"]})
        if re.search(r"供应|supply|supplier|供应链", lowered, re.I):
            slots.append({"id": "supply_chain", "required": True, "terms": ["supply", "supplier", "interruption"]})
    elif shape == "negation":
        slots.extend(
            [
                {"id": "proposed_statement", "required": True, "terms": ["no material", "not materially", "did not"]},
                {"id": "counterevidence", "required": True, "terms": ["impact", "affected", "disruption"]},
            ]
        )
    else:
        slots.append({"id": "direct_evidence", "required": True, "terms": []})
    # The plan itself must never duplicate slots when a question contains more
    # than one trigger phrase.
    unique: list[dict[str, object]] = []
    seen: set[str] = set()
    for slot in slots:
        if slot["id"] not in seen:
            unique.append(slot)
            seen.add(str(slot["id"]))
    return unique


def decomposition(question: str) -> list[str]:
    """Sub-queries for a question that names a shape rather than a phrase.

    A question like "why did revenue change" carries no lexical signal for the
    paragraph that answers it. Retrieving several thematic queries and fusing the
    results is the mechanism behind Multi-HyDE, which raised FinanceBench evidence
    page recall from 34% to 46% in published work.
    """
    text = str(question)
    if _NEG_Q.search(text):
        return _DECOMPOSITIONS["negation"]
    if _RISK_Q.search(text):
        return _DECOMPOSITIONS["risk"]
    if _CAUSE_Q.search(text):
        return _DECOMPOSITIONS["cause"]
    return []
