"""Narrow evidence-scope checks; ambiguous semantics remain the reviewer's job."""

from __future__ import annotations

import re


def sentences(text: str) -> list[str]:
    # Do not split the currency abbreviation or a decimal into separate premises.
    text = re.sub(r"\bU\.S\.", "US", text, flags=re.I)
    return [s.strip() for s in re.split(r"[。！？;；!?]|\.(?=\s|$)", text) if s.strip()]


def document_scope_conflict(statement: str) -> str | None:
    """Selected passages cannot certify an absence across an entire document.

    A directly disclosed negative fact ("the company reported no disruption")
    is different from asserting the report contains no disclosure. This only
    checks the latter; a scoped absence still needs semantic review.
    """
    document = r"\b(?:filing|report|10-[kq]|20-f)\b|年报|申报|财报|报告|公告"
    scoped = (
        r"\b(?:cited|supplied|retrieved|selected|these)\s+(?:source\s+)?(?:passages?|excerpts?|paragraphs?|text)\b"
        r"|所引(?:用)?(?:的)?(?:摘录|段落|材料|证据)|(?:已检索|已查阅|所查|这些)(?:的)?(?:段落|材料|摘录)"
    )
    absence = (
        r"\b(?:does not|did not|doesn't|didn't|never|neither)\s+(?:explicitly\s+)?"
        r"(?:contains?|states?|discloses?|mentions?|den(?:y|ies)|address(?:es)?|provides?|establish(?:es)?)\b"
        r"|\b(?:contains?|provides?|includes?)\s+no\s+(?:explicit\s+)?(?:denial|disclosure|statement|mention)\b"
        r"|(?:没有|并未|未曾|未)(?:明确)?(?:披露|提到|提及|说明|否认|记载|包含)"
    )
    exclusive_risk = (
        r"\bonly\s+(?:conditionally|as\s+(?:an?\s+)?(?:possible|future|hypothetical|potential|conditional|forward-looking)|in\s+(?:conditional|hypothetical))"
        r"|(?:仅|只).{0,45}(?:未来|可能|假设|条件)"
    )
    for sentence in sentences(statement):
        if re.search(scoped, sentence, re.I):
            continue
        if re.search(
            r"\b(?:cannot|can't)\s+(?:conclude|establish|prove|claim)\b"
            r"|(?:不能|无法|不足以)(?:断言|认定|确认|证明)", sentence, re.I,
        ):
            continue  # A limitation on making an absence claim is not that claim.
        if re.search(document, sentence, re.I) and re.search(
            absence + "|" + exclusive_risk, sentence, re.I,
        ):
            return (
                "Selected passages cannot establish document-wide absence or exclusivity. "
                "Limit the conclusion to the cited passages; distinguish no evidence found "
                "from a directly disclosed denial or counterexample."
            )
    return None


def counterevidence_conflict(question: str, statement: str, sources: list[str]) -> str | None:
    """A possible risk cannot refute absence of an actual or material impact.

    This gate covers explicit supply-chain materiality and demand-decline
    propositions. Passing it only permits review; it is not proof of refutation.
    """
    negative = r"\b(?:no|not|deny|denies|denial)\b|没有|否认|无重大"
    material_supply = bool(re.search(r"material|重大", question, re.I) and re.search(
        r"supply[- ]chain|供应链", question, re.I,
    ))
    demand = bool(re.search(r"demand.{0,25}declin|需求.{0,10}下降", question, re.I))
    if not re.search(negative, question, re.I) or not (material_supply or demand):
        return None
    counter = r"refut\w*|contradict\w*|opposite of (?:a |an |the )?denial|point.{0,12}other way|相矛盾|反驳|证伪"
    assertions = [s for s in sentences(statement) if re.search(counter, s, re.I) and not re.search(
        r"(?:cannot|can't|does not|do not|insufficient to).{0,35}(?:refut|contradict)"
        r"|(?:不能|无法|不足以).{0,12}(?:反驳|否定|证明|相矛盾)", s, re.I,
    )]
    if not assertions:
        return None
    for source in sources:
        for sentence in sentences(source):
            if material_supply:
                if (re.search(r"supply[- ]chain|供应链", sentence, re.I)
                        and re.search(r"material|重大", sentence, re.I)
                        and re.search(r"\b(?:had|caused|resulted|experienced|affected)\b|已经|造成|产生了|导致", sentence, re.I)
                        and not re.search(r"\b(?:could|may|might|would|if)\b|可能|假如|若|no material|not material|没有重大|未造成重大", sentence, re.I)):
                    return None
            elif re.search(
                r"\b(?:have|has|had) experienced.{0,90}(?:reduced|decreased|declin\w*) demand"
                r"|\bdemand\s+(?:(?:has|had)\s+)?(?:declined|decreased|fell)\b"
                r"|需求(?:已经|已|出现了)(?:下降|减少)|已(?:经)?经历.{0,15}需求下降",
                sentence, re.I,
            ) and not re.search(r"\bif\b|假如|若", sentence, re.I):
                return None
    return (
        "The proposed refutation lacks matching realized counterevidence. A possible future "
        "risk, revenue change or unquantified effect does not establish an actual demand decline "
        "or material supply-chain impact. State what the cited excerpts support and leave "
        "the proposition unresolved unless direct matching counterevidence is available."
    )


_USD = r"(?:\b(?:US\s+dollars?|USD)\b|美元)"
_WEAK = r"weak(?:en\w*|er)?|depreciat\w*|走弱|贬值"
_STRONG = r"strong(?:er)?|strengthen\w*|appreciat\w*|走强|升值"


def _usd_directions(sentence: str) -> set[int]:
    result = set()
    # Foreign currency weakness relative to USD is USD strength, and vice versa.
    for sign, direction in ((-1, _WEAK), (1, _STRONG)):
        pair = (
            rf"\b(?:{direction})(?:\s+of)?\s+foreign currencies\s+(?:relative to|against)\s+(?:the\s+)?{_USD}"
            rf"|外币(?:相对|兑)美元(?:的)?(?:{direction})"
        )
        if re.search(pair, sentence, re.I):
            result.add(-sign)
            sentence = re.sub(pair, " ", sentence, flags=re.I)
    for sign, direction in ((-1, _WEAK), (1, _STRONG)):
        if re.search(
            rf"\b(?:{direction})\s+(?:of\s+)?(?:the\s+)?{_USD}"
            rf"|{_USD}(?:'s|’s)?\s*(?:has\s+|is\s+|的)?(?:{direction})",
            sentence, re.I,
        ):
            result.add(sign)
    return result


def _pricing_scenarios(text: str) -> dict[int, set[int]]:
    scenarios: dict[int, set[int]] = {}
    price = r"international\s+pric(?:es|ing)|国际(?:售价|定价|价格)"
    for sentence in sentences(text):
        # Negated, interrogative or two-way scenarios need semantic interpretation.
        if re.search(r"\b(?:not|never|whether)\b|未必|不一定|不会|是否|不能|不是", sentence, re.I):
            continue
        for sign, direction in (
            (1, r"rais\w*|increas\w*|higher|提高|上调|上涨"),
            (-1, r"lower\w*|reduc\w*|decreas\w*|降低|下调|下跌"),
        ):
            if re.search(rf"(?:{direction})\s*(?:their\s+|our\s+|其)?(?:{price})", sentence, re.I):
                scenarios.setdefault(sign, set()).update(_usd_directions(sentence))
    return scenarios


def currency_direction_conflict(statement: str, sources: list[str]) -> str | None:
    """Detect explicit inverse USD/pricing scenarios, not arbitrary FX effects."""
    claimed = _pricing_scenarios(statement)
    observed: dict[int, set[int]] = {}
    for source in sources:
        for pricing, signs in _pricing_scenarios(source).items():
            observed.setdefault(pricing, set()).update(signs)
    for pricing, signs in claimed.items():
        if len(signs) == 1 and observed.get(pricing) == {-next(iter(signs))}:
            return (
                "The currency direction contradicts the cited pricing scenario: "
                "foreign currency weakening against USD means USD strengthening, "
                "and vice versa. Preserve the currency subject and relative direction."
            )
    return None


def narrative_completeness_errors(accepted: list[dict], question: str) -> list[str]:
    """Reject numeric-only completion of an explicit explanation/risk request.

    Presence of prose is only a necessary condition, not proof that its meaning
    covers every requested aspect. That still needs content review.
    """
    explanation = r"\b(?:why|drivers?|causes?|explain|risks?)\b|原因|归因|因素|解释|风险"
    if (accepted and re.search(explanation, question, re.I)
            and all(c["kind"] in ("fact", "calculation", "market", "valuation") for c in accepted)):
        return [
            "The requested explanation is missing: verified figures alone do not answer "
            "a cause or risk question. Add a supported explanation or disclose this specific gap."
        ]
    return []
