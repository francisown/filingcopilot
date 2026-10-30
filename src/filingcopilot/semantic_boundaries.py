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


def withheld_only(errors) -> bool:
    """True when every verifier message is a rejected claim and none is a coverage gap.

    A rejected sentence removes content but can leave the requested coverage
    intact; a coverage message means a requested item was never supported. Only
    the second kind should downgrade a delivered answer to partial.
    """
    messages = [str(error) for error in errors or []]
    return bool(messages) and all(message.startswith("claim ") for message in messages)


def nonblocking_gap(question: str, gap: str, *, full_scan: bool = False) -> bool:
    """Classify methodological caveats that should not erase a complete answer.

    These notes remain visible to callers, but a risk answer does not become
    incomplete merely because the filing gives no probability estimate, and a
    negation answer does not become incomplete solely because the search was
    excerpt-scoped. Required evidence still stays blocking.
    """
    q = str(question).casefold()
    g = str(gap).casefold()
    if "独立核验" in g or "reviewer independence" in g or "not an independent check" in g:
        return True
    risk = bool(re.search(r"风险|\brisk\w*\b|监管|\bregulat\w*\b", q, re.I))
    negation = bool(re.search(r"否认|没有重大|无重大|\bdeny\b|\bno material\b|需求下降|demand.{0,20}declin", q, re.I))
    cause = bool(re.search(r"原因|驱动|归因|利润率|\bwhy\b|\bdriver|\bcause|\bmargin", q, re.I))
    scope_note = bool(
        re.search(
            r"selected excerpts?|selected passages?|selected .* sections|not exhaustive|"
            r"document-wide|entire filing|full review|所选摘录|选定段落|未穷尽|整份文件|"
            r"其他.*未|未全面",
            g,
            re.I,
        )
    )
    unresolved = bool(
        re.search(r"remains? unresolved|cannot be determined|not resolved|仍未解决|尚未解决|"
                  r"无法确定|未能确定|仍未有结论|尚未有结论|"
                  r"没有.*披露|未找到.*明确|no passage|no explicit|not located|not retrieved|未检索到", g, re.I)
    )
    if risk and re.search(
        r"not includ(?:ed|ible) within the (?:five|5)[ -]?claim limit|"
        r"(?:未能|无法)纳入(?:五|5)条(?:断言|主张|答案)|"
        r"(?:已|达到).{0,12}(?:五|5)条(?:断言|主张|答案).{0,12}(?:上限|限制)",
        g,
        re.I,
    ) and not unresolved:
        # The answer contract intentionally bounds a broad risk brief. Omitted
        # extra categories remain visible as a note, but they do not make the
        # requested answer incomplete when every delivered claim is verified.
        return True
    if risk and re.search(r"集中度|集中风险|客户集中|单一客户|大客户|concentrat", q, re.I) and re.search(
        r"(?:no|not|without)\s+(?:numeric|quantified|quantitative)\s+"
        r"(?:single[- ]customer|customer|supplier)?\s*(?:revenue|sales)?\s*concentration|"
        r"(?:未见|未发现|没有|未披露|未给出).{0,40}(?:客户|供应商|单一|主要).{0,35}"
        r"(?:收入|销售额|营收).{0,25}(?:占比|比例|数值|量化|百分比)|"
        r"(?:客户|供应商).{0,25}(?:收入|销售额|营收).{0,25}(?:未披露|没有披露|未量化|无量化)",
        g,
        re.I,
    ) and not unresolved:
        # A risk brief may still be answered from qualitative concentration
        # evidence when the filing does not provide the requested percentage.
        # Keep the missing number visible without treating it as a missing risk
        # category.
        return True
    if negation and scope_note and not unresolved:
        return True
    if negation and full_scan and not re.search(
        r"failed evidence checks|断言未|withheld|已停止或移除|"
        r"remains? unresolved|cannot be determined|无法确定|未能确定|尚待确认|仍有待|"
        r"time budget|时间预算|research was aborted|模型请求失败",
        g,
        re.I,
    ):
        # A completed full-filing scan is the backing a negative finding needs:
        # "the filing does not deny X" is then an evidence-backed result, not an
        # unfinished search. Earlier phrasing lists kept losing to the model's
        # own words ("no passage examined", "the searched excerpts"), so the
        # default is now the scan itself, with only the three hard blocks above.
        return True
    if negation and full_scan and re.search(
        r"document-wide|entire filing|whole filing|anywhere in the filing|"
        r"exhaustive review|full filing|整份文件|整份年报|全文|穷尽扫描|全篇|"
        # The model reports the scan result in its own words: "no passage
        # examined/expressly denying…", "the searched excerpts …", "limited to
        # the cited passages". These are the same scope statement.
        r"no passage (?:examined|expressly|retrieved|directly|contains)|"
        r"targeted searches|searched (?:excerpts|passages)|"
        r"limited to the (?:cited|retrieved|searched)|not (?:established|found) "
        r"(?:among|in|by) the (?:retrieved|searched|cited)|"
        r"no statement expressly denying|no (?:explicit|affirmative) .{0,40}(?:denial|statement)",
        g,
        re.I,
    ) and not re.search(
        r"(?<!non-)(?<!non )\bmateriality\b|realized|actual|实际|实质|重大影响|"
        r"尚待确认|remains? unresolved|pending",
        g,
        re.I,
    ):
        # The deterministic prefetch has scanned every locally indexed passage
        # of the requested filing. A qualified "not found in the full local
        # scan" is a methodological note, not a missing requested fact.
        return True
    if risk and re.search(
        r"no quantified|does not quantify|qualitatively|not available as a typed|"
        r"no typed (?:structured )?(?:financial )?metric|typed numeric|"
        # The filing disclosed the category but not in the one numeric form the
        # model expected: "no numeric single-customer revenue concentration".
        # A concentration risk answered from receivables or geographic disclosure
        # is answered; the missing number is not a missing requested fact.
        r"no numeric|not disclosed numerically|no separate numeric|"
        r"(?:no|not|do(?:es)? not (?:include|disclose|provide))[^.]{0,60}"
        r"(?:numeric|quantified|quantitative)[^.]{0,60}concentration|"
        r"未量化|没有量化|概率|金额幅度|无法作为数字|数字.*未|未以数字|没有给出数字",
        g,
        re.I,
    ):
        return True
    # A risk question asks what the filing discloses, not for a particular data
    # form. Matching whole sentences made the status a wording lottery: the same
    # caveat phrased "not restated as numeric claims" instead of "no typed metric"
    # flipped an answer from answered to partial between runs.
    if risk and re.search(
        r"\btyped\b|\bstructured\b|\bquantified\b|\bquantitative\b|\bnumeric(?:al)?\b|"
        r"\bverbatim\b|\brestated\b|\bnarrative (?:text|form|disclosure)\b|"
        r"结构化|量化|叙述形式|文字形式",
        g,
        re.I,
    ) and not re.search(
        r"could not be (?:found|located)|was not retrieved|no passage|未检索到|未找到",
        g,
        re.I,
    ):
        return True
    if risk and scope_note and re.search(
        r"not exhaustive|other risk categories|selected .* excerpts?|"
        r"未穷尽|其他风险类别|其他风险因素|未全面复核|未独立复核",
        g,
        re.I,
    ) and not unresolved:
        return True
    if cause and re.search(r"optional|minor contributor|mac-specific|specific .* not retrieved|"
                           r"does not quantify each factor|未单独量化|可选|次要贡献|具体.*未检索", g, re.I):
        return True
    # Narrative questions end here by default — but not negation. For a risk or
    # cause question the model's remaining note states which disclosure it found
    # and which form it did not; deciding status from that prose made a wording
    # change flip answered to partial between runs. Retrieval coverage is
    # measured separately (span and page recall), so the answer status only
    # guards the two things prose must never do: carry a withheld claim, or
    # declare a question resolved that the evidence left open. A negation
    # question is different: "the filing never denied it" is itself an answer,
    # so an excerpt-scoped search cannot support one and the strict branches
    # above still decide.
    if (risk or cause) and not re.search(
        r"failed evidence checks|断言未|withheld|已停止或移除|"
        r"remains? unresolved|cannot be determined|无法确定|未能确定|尚待确认|仍有待|"
        r"time budget|时间预算|research was aborted|模型请求失败|"
        # "The work is not finished" is a confession about the deliverable and
        # must block; "the filing does not disclose it" is a fact about the
        # filing and is a scope note. Confusing the two graded a missing
        # requested explanation as delivered.
        r"尚未完成|未完成|not (?:yet )?completed|did not submit|未提交|incomplete",
        g,
        re.I,
    ):
        return True
    return False
