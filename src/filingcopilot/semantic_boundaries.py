"""Narrow evidence-scope checks; ambiguous semantics remain the reviewer's job."""

from __future__ import annotations

import re


def complete_evidence_quote(quote: str, source: str) -> bool:
    """Reject clipped decisive citations; semantic scope still needs review.

    An exact substring may omit the condition that follows it, or start after
    a period qualifier. Require sentence boundaries on both ends, including
    when the retrieved passage itself ends in the middle of a sentence.
    """
    quote = quote.strip()
    if not quote or not quote.rstrip('\"\u201d\u2019)]').endswith((".", "!", "?", "\u3002", "\uff01", "\uff1f")):
        return False
    for match in re.finditer(re.escape(quote), source):
        before = source[:match.start()]
        if not before.strip() or before.endswith(("\n", "\r")):
            return True
        if before.rstrip().rstrip('\"\u201d\u2019)]').endswith((".", "!", "?", "\u3002", "\uff01", "\uff1f")):
            return True
    return False


def sentences(text: str) -> list[str]:
    # Do not split the currency abbreviation or a decimal into separate premises.
    text = re.sub(r"\bU\.S\.", "US", text, flags=re.I)
    return [s.strip() for s in re.split(r"[。！？;；!?]|\.(?=\s|$)", text) if s.strip()]


def resolution_period_supported(question: str, anchor: str, source: str, years: set[int]) -> bool:
    """Require source period words; distinguish a report-date state from a fiscal event."""
    if not isinstance(anchor, str) or anchor not in source or not complete_evidence_quote(anchor, source):
        return False
    as_of_report = r"as of (?:the )?(?:date of (?:this|the) (?:annual )?report|report date)|截至(?:本|该)?(?:年报|报告)(?:发布)?日"
    if re.search(as_of_report, question, re.I) and re.search(as_of_report, anchor, re.I):
        return True
    return set(years) <= {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", anchor)}


def document_scope_conflict(statement: str) -> str | None:
    """Selected passages cannot certify an absence across an entire document.

    A directly disclosed negative fact ("the company reported no disruption")
    is different from asserting the report contains no disclosure. This only
    checks the latter; a scoped absence still needs semantic review.
    """
    document = r"\b(?:filing|report|10-[kq]|20-f|company|management)\b|年报|申报|财报|报告|公告|公司|管理层"
    scoped = (
        r"\b(?:cited|supplied|retrieved|selected|reviewed|quoted|examined|observed|these)\s+(?:source\s+)?(?:passages?|excerpts?|paragraphs?|text)\b"
        r"|所引(?:用)?(?:的)?(?:摘录|段落|片段|原文|材料|证据)"
        r"|(?:已检索|已查阅|所查|这些)(?:的)?(?:段落|片段|原文|材料|摘录)"
    )
    absence = (
        r"\b(?:does not|did not|doesn't|didn't|never|neither)\s+(?:explicitly\s+)?"
        r"(?:contains?|states?|discloses?|mentions?|den(?:y|ies)|address(?:es)?|provides?|establish(?:es)?|quantif(?:y|ies)|classif(?:y|ies))\b"
        r"|\b(?:contains?|provides?|includes?)\s+no\s+(?:explicit\s+)?(?:denial|disclosure|statement|mention)\b"
        r"|(?:没有|并未|未曾|未)(?:明确)?(?:披露|提到|提及|说明|否认|记载|包含)"
        r"|(?:没有|并未|未)(?:就|在)[^。；;.!?]{0,35}(?:提供|给出|量化)"
        r"|(?:没有|并未|未)(?:提供|给出)[^。；;.!?]{0,35}(?:量化|披露|数据|说明|评估|结论|否定|确认|表述)"
    )
    exclusive_risk = (
        r"\bonly\s+(?:conditionally|as\s+(?:an?\s+)?(?:possible|future|hypothetical|potential|conditional|forward-looking)|in\s+(?:conditional|hypothetical))"
        r"|(?:仅|只).{0,45}(?:未来|可能|假设|条件)"
        r"|\bonly\s+(?:discloses?|states?|provides?|mentions?)\b|(?:仅|只)(?:披露|说明|提供|给出)"
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


def expense_classification_conflict(statement: str, sources: list[str]) -> str | None:
    """A disclosed charge alone does not establish nonrecurring treatment.

    This is a necessary source condition, not approval: the reviewer still
    checks whether the classification applies to the particular cited expense.
    """
    classification = r"\bone[- ](?:time|off)\b|\bnon[- ]?recurring\b|一次性|非经常性"
    expense = r"\b(?:charges?|expenses?|costs?|losses?|gains?|items?)\b|费用|成本|支出|项目|损益|损失|收益"
    if any(re.search(classification, source, re.I) for source in sources):
        return None
    for clause in re.split(r"[,，;；。!?！？]|\.(?=\s|$)", statement):
        # A statement that the classification is unproved is not asserting it.
        assertion = re.sub(
            r"(?:cannot|can't|does not|do not|insufficient to)\s+(?:establish|prove|classify|treat|label)"
            r"[^,，;；。.!?]{0,90}?(?:" + classification + r")"
            r"|(?:whether|not necessarily)[^,，;；。.!?]{0,90}?(?:" + classification + r")"
            r"|(?:不能|无法|不足以|尚不能|未经证实|是否|未必|不一定)[^,，;；。.!?]{0,35}?(?:" + classification + r")",
            "", clause, flags=re.I,
        )
        if re.search(classification, assertion, re.I) and re.search(expense, assertion, re.I):
            return "Classifying charges as one-time/nonrecurring requires an explicit source classification; a settlement, severance or impairment alone does not establish recurrence. Remove the added label."
    return None


def causal_attribution_conflict(statement: str, sources: list[str]) -> str | None:
    """A narrative attribution needs a relation in the source, not co-occurrence.

    This is only a necessary condition. When relation language is present,
    semantic review must still bind its actual subject, effect and scope.
    Reasoned accounting analysis is checked separately from narrative quotes.
    """
    connective = r"\b(?:therefore|thus|hence|consequently|because|due to|as a result|led to|resulted in)\b|因此|因而|从而|所以|由于|导致"
    if not re.search(connective, statement, re.I):
        return None
    attribution = (
        r"\b(?:because|due to|driven|attribut\w*|reflect\w*|effect\w*|impact\w*|affect\w*|"
        r"caus\w*|led to|lead(?:s|ing)? to|(?:give|gives|giving|gave) rise to|trigger\w*|requir\w*|"
        r"result\w* (?:in|from)|contribut\w*|support\w*|offset\w*|"
        r"enabl\w*|prevent\w*|depend\w*|limited by|as a result)\b"
        r"|\bconstrain(?:s|ed)?\s+(?:our|the|their)\b"
        r"|因为|由于|导致|带来|使得|归因|反映|推动|影响|抵消|受制于"
    )
    if any(re.search(attribution, source, re.I) for source in sources):
        return None
    for sentence in sentences(statement):
        if not re.search(connective, sentence, re.I):
            continue
        if re.search(
            r"(?:therefore|thus|hence|consequently).{0,30}(?:cannot|does not|do not|not enough|insufficient)"
            r"|(?:因此|因而|所以).{0,12}(?:无法|不能|不足以|尚未|不代表)", sentence, re.I,
        ):
            continue
        return (
            "The cited passage reports observations without a causal attribution. Do not turn "
            "co-occurrence into because/therefore. Preserve the separate observations or cite "
            "a source that actually supports the causal link; plausible causation is insufficient. "
            "For an accounting identity inferred from disclosed components, submit kind=analysis "
            "with all supporting component evidence; do not present it as management's narrative attribution."
        )
    return None


def currency_bridge_conflict(statement: str, sources: list[str]) -> str | None:
    """Do not read a constant-currency conversion adjustment as a reported effect."""
    def bridge(text: str) -> bool:
        return all(re.search(pattern, text, re.I) for pattern in (
            r"as\s+reported", r"(?:at\s+)?prior\s+year\s+rates", r"exchange\s+rate\s+effect",
        ))

    if not any(bridge(source) for source in sources):
        return None
    currency = r"exchange\s+rates?|currenc(?:y|ies)|\bFX\b|汇率|外汇"
    direction = (
        r"\b(?:increas\w*|decreas\w*|reduc\w*|lower\w*|higher|headwind|tailwind|drag|boost\w*)\b"
        r"|增加|减少|提高|降低|低于|高于|上升|下降|拖累|推动|支撑|抵消"
    )
    uncertain = r"\bwhether\b|cannot\s+(?:establish|conclude)|(?:不能|无法|不足以)(?:据此)?(?:认定|确认|证明)|是否"
    assertions = [s for s in sentences(statement) if re.search(currency, s, re.I)
                  and re.search(direction, s, re.I) and not re.search(uncertain, s, re.I)]
    if not assertions:
        return None
    # A separately disclosed prose explanation may support the direction;
    # the reviewer still checks its metric, period and scope.
    if any(re.search(currency, sentence, re.I) and re.search(direction, sentence, re.I)
           for source in sources for sentence in sentences(source) if not bridge(sentence)):
        return None
    return (
        "A constant-currency bridge adjustment cannot by itself establish the direction of "
        "the reported currency effect. Its columns may convert reported results to prior-year "
        "rates, which reverses the sign. Use an explicit source explanation or a typed calculation "
        "with bound column operands; otherwise omit this inferred driver and retain supported explanations."
    )


def customer_diversification_conflict(statement: str, sources: list[str]) -> str | None:
    """Account, product and geographic categories do not measure customer concentration."""
    absolute = r"\bno\s+(?:single[- ]customer\s+|customer\s+)?concentration\b|(?:未出现|没有|不存在|无)(?:单一)?客户集中"
    ranking = r"(?:customer\s+)?concentration(?:\s+risk)?.{0,35}(?:primarily|mainly)|客户.{0,12}集中.{0,12}(?:主要|仅)"
    exclusion = (r"\bconcentration\b[^.;。]{0,65}(?:rather than|not)\s+(?:in\s+)?(?:revenue|receivables)\b"
                 r"|集中(?:度|风险)?[^。；]{0,35}而非(?:收入|应收账款)")
    uncertain = r"(?:cannot|does not|do not)\s+(?:establish|prove|imply|show)|(?:不能|无法|不足以)(?:据此)?(?:断言|认定|确认|证明)"
    for clause in sentences(statement):
        if re.search(uncertain, clause, re.I):
            continue
        pattern = next((p for p in (absolute, ranking, exclusion) if re.search(p, clause, re.I)), None)
        if pattern and not any(re.search(pattern, s, re.I) for s in sources):
            return (
                "A customer disclosure threshold does not establish no customer concentration, "
                "or rank receivable risk above revenue concentration risk. Preserve the disclosed "
                "threshold and dimensions without that added absence or ranking conclusion."
            )
    diversified = (
        r"\b(?:diversified|diverse)\s+customer\s+base\b"
        r"|\bcustomer\s+base\b.{0,25}\b(?:diversified|diverse)\b"
        r"|\b(?:low|limited)\s+customer\s+concentration\b"
        r"|\bno\s+(?:single|individual|dominant|major)\s+customer\b"
        r"|\brather than\s+(?:one|a|a single)\s+dominant\s+customer\b"
        r"|\bnot\s+dependent\s+on\s+(?:a\s+)?single\s+customer\b"
        r"|客户集中度.{0,5}(?:低|不高)|客户(?:群|基础)?.{0,5}分散|不依赖单一客户"
    )
    uncertain = (
        r"\bwhether\b|(?:cannot|does not|do not)\s+(?:establish|prove|imply|show)"
        r"|(?:不能|无法|不足以)(?:据此)?(?:断言|认定|确认|证明)|是否"
    )
    if not any(re.search(diversified, clause, re.I) and not re.search(uncertain, clause, re.I)
               for clause in sentences(statement)):
        return None
    customer_distribution = (
        r"\b(?:single|individual|largest|major|direct|indirect|top\s+\w+)\s+customers?\b"
        r"|\bno\s+customers?\b"
        r"|\bcustomer\s+concentration\b|\b(?:diversified|diverse)\s+customer\s+base\b"
        r"|\bcustomer\s+[A-Z0-9]\b|\blimited\s+number\s+of\s+customers\b"
        r"|客户集中度|(?:单一|个别|最大|主要|前\w+)客户|客户基础.{0,5}分散"
    )
    if any(re.search(customer_distribution, source, re.I) for source in sources):
        return None  # Necessary dimension only; reviewer must check the actual distribution.
    return (
        "Customer diversification requires evidence about individual customers or an explicit "
        "customer-concentration disclosure. Geographic segments, product lines and receivable "
        "account categories do not establish the absence of a dominant customer. Describe those "
        "disclosed dimensions without inferring customer diversification."
    )


def nonoccurrence_conflict(statement: str, sources: list[str]) -> str | None:
    """A prospective risk is not proof that the event never materialized.

    This is a necessary source condition for explicit nonoccurrence wording;
    the reviewer still checks the event, time and scope of the negative fact.
    """
    absent = (
        r"\b(?:has|have|had)\s+not\s+(?:yet\s+)?(?:occurred|happened|materialized)\b"
        r"|\b(?:did|does)\s+not\s+(?:occur|happen|materialize)\b"
        r"|(?:尚未|并未|没有|未曾|未)(?:实际|真正)?发生"
        r"|\brather than\s+(?:actual|realized)\s+(?:harm|damage|impact|loss)\b"
        r"|而非(?:已实现|已发生|实际)(?:损害|影响|损失)"
        r"|\b(?:no|without)\s+(?:an?\s+)?(?:(?:aggregate|overall|underlying|customer)\s+)?demand\s+(?:decline|decrease|reduction)\b"
        r"|\bno\s+(?:decline|decrease|reduction)\s+in\s+(?:(?:aggregate|overall|underlying|customer)\s+)?demand\b"
        r"|\bdemand\s+(?:has|have|had|did)\s+not\s+(?:declined?|decreased?|fallen|fall)\b"
        r"|需求(?:并未|没有|未曾|未)(?:出现)?(?:下降|减少|下滑)"
        r"|(?:没有|未)(?:出现|发生)?(?:总体|整体|客户)?需求(?:下降|减少|下滑)"
    )
    uncertain = (
        r"(?:cannot|can't|does not|do not)\s+(?:establish|prove|conclude|show)"
        r"|\bwhether\b|(?:不能|无法|不足以)(?:据此)?(?:断言|认定|确认|证明)|是否"
    )
    # Discussing an absent denial is not asserting that the event never happened.
    # Remove just that phrase, so a separate actual assertion still gets checked.
    denial_discussion = (
        r"(?:并非|而非|不是|不构成)(?:对)?(?:企业|公司|整体|总体|本期|当前|的|\s)*"
        r"需求(?:并未|没有|未曾|未)(?:出现)?(?:下降|减少|下滑)(?:的)?(?:明确)?(?:否认|确认|证明)"
        r"|(?:not|rather than)\s+(?:an?\s+)?(?:explicit\s+)?(?:denial|confirmation|proof)\s+"
        r"(?:that|of)\s+(?:(?:overall|aggregate|underlying|customer)\s+)?"
        r"(?:demand\s+(?:has|had|did)\s+not\s+(?:declined?|decreased?|fallen)"
        r"|no\s+(?:demand\s+decline|decline in demand))"
    )
    asserted = any(
        re.search(absent, clause, re.I) and not re.search(uncertain, clause, re.I)
        for clause in sentences(re.sub(denial_discussion, "", statement, flags=re.I))
    )
    if not asserted:
        return None
    explicit_negative = (
        absent + r"|\bno\b.{0,70}\b(?:has|have|had)\s+(?:occurred|happened|materialized)\b"
        r"|\b(?:have|has|had)\s+not\s+(?:experienced|suffered|incurred)\b"
        r"|\b(?:demand|sales)\s+(?:has|have|had)\s+not\s+(?:declined|decreased|fallen)\b"
        r"|未(?:曾)?(?:经历|遭受)|(?:需求|销售)(?:并未|没有|未曾|未)(?:下降|减少)"
    )
    for source in sources:
        for clause in sentences(re.sub(denial_discussion, "", source, flags=re.I)):
            if re.search(explicit_negative, clause, re.I) and not re.search(
                uncertain + r"|\b(?:if|unless|whether)\b|如果|假如|若", clause, re.I,
            ):
                return None
    return (
        "A conditional or prospective risk does not prove nonoccurrence. The cited evidence "
        "must explicitly establish that the event has not occurred; otherwise state that "
        "whether it occurred remains unestablished."
    )


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
    counter = (
        r"refut\w*|contradict\w*|inconsistent with|incompatible with|"
        r"counterexample|\bopposite\b|\bcontrary to\b|point.{0,12}other way|"
        r"相矛盾|相悖|相反|相抵触|反向|反驳|证伪|不一致|不相容|不(?:相)?符|反例|反证"
    )
    # A discourse transition contrasts what the excerpts discuss; it does not
    # itself assert that the requested proposition is false. Keep predicates
    # such as "披露方向相反" intact for the materiality check.
    clauses = [re.sub(r"(^|[,，])\s*相反\s*[,，:：]\s*", r"\1", s) for s in sentences(statement)]
    assertions = [s for s in clauses if re.search(counter, s, re.I) and not re.search(
        r"(?:cannot|can't|does not|do not|insufficient to).{0,35}(?:refut|contradict|inconsistent|incompatible)"
        r"|\bnot\s+(?:inconsistent|incompatible|a counterexample)\b"
        r"|(?:cannot|can't|does not|do not|insufficient to).{0,60}(?:opposite|contrary)"
        r"|\bnot\s+(?:contrary|opposite)\b"
        r"|(?:cannot|can't|does not|do not|insufficient to).{0,50}counterexample"
        r"|(?:不能|无法|不足以).{0,25}(?:反驳|否定|证明|相矛盾|相悖|相反|相抵触|反向|不一致|不相容|不(?:相)?符|反例|反证)", s, re.I,
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


def ratio_precision_conflict(statement: str, sources: list[str]) -> str | None:
    """Equal displayed percentages do not prove equal underlying ratios."""
    ratio = r"\b(?:ratio|margin|percentage|proportion|share)\b|占比|比例|费用率|利润率|毛利率"
    flat = r"\b(?:unchanged|flat|the same)\b|持平|不变|相同|未变"
    assertion = rf"(?:{ratio}).{{0,65}}(?:{flat})"
    precision = r"round(?:ed|ing)|display precision|displayed precision|reported precision|四舍五入|取整|展示精度|列示精度"
    relevant = [s for s in sentences(statement) if re.search(assertion, s, re.I)
                and not re.search(precision, s, re.I)
                and not re.search(r"cannot|can't|does not establish|do not establish|不能|无法|不足以", s, re.I)]
    if not relevant:
        return None
    # An explicit source statement can be paraphrased, subject to semantic
    # scope review. A table alone needs a precision qualifier or exact inputs.
    if any(re.search(assertion, s, re.I) for source in sources for s in sentences(source)):
        return None
    for source in sources:
        percentages = re.findall(r"(?<![\d.])\d+(?:\.\d+)?\s*[%％]", source)
        labels = [re.sub(r"\s+", "", value).replace("％", "%") for value in percentages]
        if len(labels) != len(set(labels)):
            return (
                "Equal displayed percentage labels do not establish an unchanged underlying ratio. "
                "State explicitly that equality is at the table's rounded display precision, "
                "or calculate and cite the exact comparable amounts. Do not infer a zero contribution "
                "to a margin change from rounded labels."
            )
    return None


def gap_scope_conflict(question: str, gap: str) -> str | None:
    """Limitations cannot bypass the citation checks applied to answer claims."""
    if conflict := document_scope_conflict(gap):
        return conflict
    if counterevidence_conflict(question, gap, []):
        return (
            "A limitation note cannot assert uncited support or refutation. Put any proposed "
            "counterexample in a cited answer claim for verification; otherwise describe only "
            "the unresolved evidence scope."
        )
    return None


def scope_gap_present(gaps: list[str]) -> bool:
    """True when one gap already states the examined-evidence boundary.

    The scope guard replaces an unsafe document-wide limitation with a bounded
    one. Appending that bounded note next to an equivalent author or review
    gap repeats the same limitation and makes a partial answer look noisier
    than the evidence warrants.
    """
    scope = re.compile(
        r"整份(?:文件|申报|财报)|全篇|未检索|未穷尽|所选(?:摘录|段落|章节)|"
        r"document-wide|entire filing|whole filing|filing-wide|"
        r"selected (?:excerpts?|passages?|sections?)|"
        r"not exhaustive|full review|other .* not reviewed|"
        r"所(?:检索|引用|查看)的?(?:证据|段落|摘录).{0,40}(?:不足|不能|无法|未)",
        re.I,
    )
    return any(scope.search(str(gap)) for gap in gaps)


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
    """Reject evidence-only completion of an explicit narrative request.

    Presence of an excerpt is only a necessary source check, not an answer. A
    cause, risk, or negation question needs at least one synthesized narrative
    or analysis claim. Otherwise a numeric fact or a pasted source passage can
    be incorrectly reported as ``answered`` after review removes the model's
    actual conclusion.
    """
    explanation = r"\b(?:why|drivers?|causes?|explain|risks?)\b|原因|归因|因素|解释|风险"
    negation = (
        r"\b(?:deny|denies|denied|no material|without material|did not state|"
        r"does not state|whether the filing)\b"
        r"|否认|没有重大|无重大|是否(?:明确)?(?:披露|说明|否认)"
    )
    requires_synthesis = bool(re.search(f"(?:{explanation})|(?:{negation})", question, re.I))
    if (
        accepted
        and requires_synthesis
        and not any(c["kind"] in ("analysis", "narrative") for c in accepted)
    ):
        return [
            "The requested explanation/narrative conclusion is missing: verified figures or source excerpts "
            "alone do not answer a cause, risk, or negation question. Add a supported synthesis "
            "or disclose this specific gap."
        ]
    return []


def unresolved_proposition_gaps(accepted: list[dict], question: str) -> list[str]:
    """A supported statement of uncertainty does not settle a requested denial.

    Check explicit admissions about the asked proposition, not ordinary future
    risk uncertainty. This is a conservative status guard, not an entailment
    checker: a passing answer still needs independent evidence review.
    """
    if not re.search(
        r"\b(?:deny|denies|denied|no material|without material|did not state|does not state)\b"
        r"|否认|没有重大|无重大|是否(?:明确)?(?:披露|说明)", question, re.I,
    ):
        return []
    unresolved = (
        r"\b(?:proposition|denial|requested conclusion|asked question)\b.{0,70}"
        r"\b(?:unresolved|undetermined|unconfirmed|not established|not determined|not settled)\b"
        r"|(?:是否|所问(?:的)?(?:主张|问题)|该命题).{0,60}"
        r"(?:无法确定|不能确定|尚未确定|尚未解决|仍未解决|未能证实)"
    )
    for claim in accepted:
        if claim.get("kind") not in {"narrative", "analysis"}:
            continue
        text = re.sub(
            r"\b(?:rather than(?:\s+left)?|not|never)\s+(?:unresolved|undetermined|unconfirmed|not established)\b"
            r"|(?:并非|不是|并不)(?:尚未解决|仍未解决|未能证实)", "", claim.get("text", ""), flags=re.I,
        )
        if any(re.search(unresolved, sentence, re.I) for sentence in sentences(text)):
            return [
                "所问命题仍未解决；已引用的证据支持局部观察，但不足以确认或反驳所问主张。"
                if re.search(r"[\u4e00-\u9fff]", question)
                else "The requested proposition remains unresolved: the cited evidence supports scoped observations but does not settle the requested claim."
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


def _coverage_supported_caveat(gap: str) -> bool:
    """Quantification/source-display caveats after a passing completeness review."""
    return bool(
        re.search(
            r"no quantif|does not quantif|not quantified|quantif|quantit|量化|"
            r"no .* bridge|not .* bridge|桥接|分解|decomposition|"
            r"selected set|not exhaustive|篇幅|claim limit|"
            r"excerpt.*continues|displayed text|remainder .* not reviewed|truncated|截断|"
            r"no segment-level|no .* amount|未给出.*金额|"
            r"gross_profit|gross margin|毛利率|毛利金额|毛利层面|限于.*口径|"
            r"limited to .* basis|alternate .* basis|"
            r"some details remain unresolved|所检索证据尚不足以确认部分细节|"
            r"cannot .* entire filing|不能据此认定整份文件|"
            r"did not submit a complete answer|研究预算|提交完整回答",
            gap,
            re.I,
        )
    )


def _negative_scan_caveat(gap: str) -> bool:
    """Residual scope notes after a deterministic full-filing negative scan."""
    return bool(
        re.search(
            r"paraphras|wording other than|other wording|同义|改写|语义核查|"
            r"semantic (?:review|absence)|quantif|quantit|量化|actual (?:impact|effect)|"
            r"realized (?:impact|effect)|已实现|实际影响|explicit pattern|keyword|关键词|"
            r"索引措辞|indexed wording|"
            r"所检索证据尚不足以确认部分细节|不能据此认定整份文件没有相关披露|"
            r"部分问题仍未解决|草稿对这一限制的原因解释未通过核验|"
            r"some details remain unresolved|document-wide absence|"
            r"actual .* outcome|does not establish an actual|"
            r"actual demand|realized .* demand|demand trend|demand direction|实际需求|实际走向|"
            r"实际发生|是否实际|实际影响|是否重大|重大性|actual occurrence|actually occurred|"
            r"actual .* assessment|materiality assessment|actual FY|"
            r"a required point remains unresolved|"
            r"did not submit a complete answer|研究预算|提交完整回答|"
            r"(?:proposition|requested conclusion|asked question).{0,70}"
            r"(?:unresolved|not established|not settled)|"
            r"(?:所问|该命题).{0,60}(?:仍未解决|尚未解决|无法确定|未能确定)",
            gap,
            re.I,
        )
    )


def nonblocking_gap(
    question: str,
    gap: str,
    *,
    full_scan: bool = False,
    negative_resolved: bool = False,
    coverage_supported: bool = False,
) -> bool:
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
    cause = bool(re.search(r"为什么|原因|驱动|归因|利润率|\bwhy\b|\bdriver|\bcause|\bmargin", q, re.I))
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
        re.search(r"remains? unresolved|cannot be determined|not resolved|not settled|not established|not been established|do not prove|仍未解决|尚未解决|"
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
    if coverage_supported and _coverage_supported_caveat(g):
        # The independent completeness review already found that the delivered
        # answer covers the question. A quantification, source-truncation or
        # submission-limit caveat is a visible quality note, not a missing
        # requested item.
        return True
    if negative_resolved and negation and _negative_scan_caveat(g):
        # A deterministic full-filing scan has already answered the bounded
        # "no explicit statement" question. A residual note about paraphrases
        # or the semantic limit of the scan stays visible, but it is not a
        # missing requested item.
        return True
    # A keyword scan covers indexed text, not every possible paraphrase.
    # It cannot waive an unresolved proposition or certify document-wide absence.
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
    # Unclassified omissions stay blocking; prose shape is not proof of coverage.
    return False
