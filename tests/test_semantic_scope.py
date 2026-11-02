"""Semantic boundaries must survive an erroneous approval from a reviewer."""

import json

import pytest

from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission

FX_SOURCE = (
    "Weaker foreign currencies relative to the U.S. dollar can lead us to raise "
    "international prices, which may reduce demand."
)


@pytest.mark.parametrize("statement", [
    "Increased segment unit sales are a counterexample to a generalized demand-decline claim.",
    "分部销量上升构成对整体需求下降的反例。",
])
def test_sales_growth_is_not_a_counterexample_to_a_demand_proposition(tools, statement):
    from filingcopilot.semantic_boundaries import counterevidence_conflict
    assert counterevidence_conflict("Does the filing deny a demand decline?", statement,
                                    ["Segment unit sales increased."])
    accepted, errors = check(tools, statement, "Segment unit sales increased.",
                             "Does the filing deny a demand decline?")
    assert not accepted and any("counterevidence" in e for e in errors)


@pytest.mark.parametrize("statement", [
    "No customer crossed the disclosure threshold, so there is no customer concentration.",
    "无单一客户超过披露门槛，即收入端未出现单一客户集中。",
    "Customer concentration risk primarily lies in receivables rather than revenue.",
    "因此客户集中风险主要体现在应收账款维度而非单一客户收入占比。",
    "客户层面的集中主要体现在应收账款集中于少数客户。",
    "说明集中度体现在应收账款维度而非收入维度。",
    "Concentration lies in receivables, not revenue.",
])
def test_threshold_does_not_establish_absence_or_rank_concentration_risk(tools, statement):
    source = "No customer represented 10% or more of total revenue. Two customers represented 22% of accounts receivable."
    accepted, errors = check(tools, statement, source, "Discuss customer concentration risk.")
    assert not accepted and any("concentration" in e for e in errors)


@pytest.mark.parametrize("statement", [
    "No customer crossed the disclosed revenue threshold; receivables had two significant customers.",
    "收入端无单一客户达到披露门槛，应收账款端有两名重要客户。",
    "The threshold alone does not establish an absence of concentration risk.",
    "The concentration disclosure covers receivables; no customer met the revenue threshold.",
    "集中度披露涉及应收账款；收入端没有单一客户达到披露门槛。",
    "The source states that no single customer crossed the disclosed revenue threshold.",
])
def test_threshold_qualified_comparison_remains_reviewable(tools, statement):
    accepted, errors = check(tools, statement,
        "No customer represented 10% or more of total revenue. Two customers represented 22% of accounts receivable.",
        "Discuss customer concentration risk.")
    assert accepted, errors


def test_absence_of_classification_must_be_scoped_to_examined_passages():
    from filingcopilot.semantic_boundaries import document_scope_conflict
    statement = "The filing does not quantify or classify any past incident as material."
    assert document_scope_conflict(statement)
    assert not document_scope_conflict("In the cited passages, " + statement)


def test_limitation_cannot_launder_an_uncited_counterexample():
    from filingcopilot.semantic_boundaries import gap_scope_conflict
    question = "Does the filing deny a demand decline?"
    assert gap_scope_conflict(question, "The conclusion rests on revenue growth drivers that contradict a demand decline.")
    assert not gap_scope_conflict(question, "Revenue growth cannot refute a demand decline; the proposition remains unresolved.")


@pytest.mark.parametrize("statement", [
    "Increased unit sales are not a counterexample to a demand decline elsewhere.",
    "分部销量上升不能构成对其他业务需求下降的反例。",
])
def test_uncertainty_about_a_counterexample_remains_reviewable(tools, statement):
    accepted, errors = check(tools, statement, "Segment unit sales increased.",
                             "Does the filing deny a demand decline?")
    assert accepted, errors


@pytest.mark.parametrize("statement", [
    "公司未就营业利润率变化单独提供定量归因或利润桥接。",
    "合并层面只披露了汇率影响，因此需要由分部综合而非申报的直接归因。",
    "The filing only discloses currency effects at the consolidated level.",
])
def test_unrequested_bridge_disclaimers_cannot_assert_document_wide_absence(statement):
    from filingcopilot.semantic_boundaries import document_scope_conflict
    assert document_scope_conflict(statement)
    assert not document_scope_conflict("In the cited passages, " + statement)


def check(tools, statement, source=FX_SOURCE, question="Discuss Apple's risks."):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["p1"]["text"] = source
    return verify_submission(
        Submission(claims=[Claim(kind="narrative", statement=statement, evidence_ids=["p1"])]),
        tools, question,
        lambda question, claims: {"verdicts": [
            {"index": c["index"], "supported": True, "reason": "Erroneous approval control"}
            for c in claims
        ]},
    )


@pytest.mark.parametrize("statement", [
    "美元走弱会促使公司提高国际售价而可能压低需求。",
    "A weaker U.S. dollar could lead the company to raise international prices.",
    "US dollar depreciation may result in higher international pricing.",
])
def test_inverse_currency_subject_cannot_reverse_the_pricing_scenario(tools, statement):
    accepted, errors = check(tools, statement)
    assert not accepted
    assert any("currency direction" in error for error in errors)


@pytest.mark.parametrize("statement,source", [
    ("美元走强可能促使公司提高国际售价。", FX_SOURCE),
    ("外币相对美元走弱可能促使公司提高国际售价。", FX_SOURCE),
    ("A stronger US dollar may lead to higher international pricing.", FX_SOURCE),
    ("Dollar depreciation may lead to lower international prices.", FX_SOURCE),
    ("A weaker euro could lead to higher international prices.", FX_SOURCE),
    ("美元走弱可能提高国际售价。", "A weakening U.S. dollar may cause higher international prices."),
    ("美元走弱可能提高国际售价。", "Currencies fluctuate; pricing depends on market conditions."),
    ("美元走弱未必会提高国际售价。", FX_SOURCE),
    ("原文不是说美元走弱会提高国际售价。", FX_SOURCE),
    ("美元走弱可能提高国际售价。", FX_SOURCE + " A weaker USD can also lead to higher international prices."),
])
def test_currency_gate_preserves_correct_or_ambiguous_scenarios(tools, statement, source):
    accepted, errors = check(tools, statement, source)
    assert accepted and not errors


@pytest.mark.parametrize("statement", [
    "The annual filing does not contain an explicit denial of a demand decline.",
    "The filing's risk factors discuss demand decline only conditionally.",
    "Demand decrease appears in the filing only as a hypothetical risk.",
    "The report never states that supply-chain effects were material.",
    "年报没有披露重大供应链影响。",
    "该申报仅把需求下降描述为未来可能发生的风险。",
    "所引摘录描述了强劲需求。年报没有提到实际需求下降。",
])
def test_selected_passages_cannot_establish_document_wide_absence(tools, statement):
    accepted, errors = check(tools, statement, "Demand for one product was strong. Other products face risks.")
    assert not accepted
    assert any("document-wide" in error for error in errors)


@pytest.mark.parametrize("statement", [
    "The cited passages do not establish whether supply-chain effects were material.",
    "In the cited passages, demand decline is described only as a future possibility.",
    "所引摘录不足以确认供应链影响是否重大。",
    "在已检索段落中，未见明确否认需求下降的表述。",
    "The filing explicitly states that supply disruption had no material impact.",
    "The company reported that no material supply disruption occurred.",
    "年报明确披露供应链中断未造成重大影响。",
    "We cannot conclude that the filing does not disclose any impact.",
    "不能断言年报没有披露供应链影响。",
])
def test_scoped_uncertainty_and_direct_negative_disclosures_remain_reviewable(tools, statement):
    accepted, errors = check(tools, statement, "The company reported no material supply disruption.")
    assert accepted and not errors


@pytest.mark.parametrize("question", [
    "Why did Apple's revenue change in FY2025?",
    "苹果 FY2025 营收增长由哪些因素解释？",
    "What regulatory and demand risks does Apple disclose?",
    "苹果的集中度风险有哪些？",
])
def test_figures_alone_cannot_complete_an_explanatory_question(tools, question):
    fact = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[fact["id"]])]), tools, question,
    )
    assert accepted, "Keep valid figures; incomplete coverage does not invalidate them"
    assert any("explanation" in error for error in errors)


def test_numeric_question_and_disclosed_partial_remain_valid(tools):
    fact = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    claim = Claim(kind="fact", evidence_ids=[fact["id"]])
    for question, gaps in [
        ("Apple FY2025 revenue", []),
        ("Why did Apple's revenue change?", ["The cause has not been established."]),
    ]:
        accepted, errors = verify_submission(Submission(claims=[claim], gaps=gaps), tools, question)
        assert accepted and not errors


@pytest.mark.parametrize("question", [
    "What risks does Apple disclose?",
])
def test_numeric_only_delivery_keeps_an_explicit_explanation_gap(tools, question):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest
    from filingcopilot.settings import Settings

    fact = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert not review, "No prose has been submitted to review"
            return {"message": {"tool_calls": [{"id": "submit", "type": "function", "function": {
                "name": "submit_answer", "arguments": json.dumps({
                    "claims": [{"kind": "fact", "evidence_ids": [fact["id"]]}], "gaps": [],
                }),
            }}]}, "model": "author"}

    settings = Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1, min_model_interval=0)
    answer = ResearchAgent(settings, Gateway()).run(ResearchRequest(
        question=question, ticker="AAPL", as_of=tools.as_of, language="en",
    ), tools)
    assert answer.answer and answer.status == "partial"
    assert any("explanation is still missing" in gap for gap in answer.gaps)
    assert any(t["stage"] == "answer_coverage" for t in answer.trace)


def test_numeric_only_negation_gets_a_bounded_deterministic_answer(tools):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest
    from filingcopilot.settings import Settings

    fact = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert not review, "No prose has been submitted to review"
            return {"message": {"tool_calls": [{"id": "submit", "type": "function", "function": {
                "name": "submit_answer", "arguments": json.dumps({
                    "claims": [{"kind": "fact", "evidence_ids": [fact["id"]]}], "gaps": [],
                }),
            }}]}, "model": "author"}

    settings = Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1, min_model_interval=0)
    answer = ResearchAgent(settings, Gateway()).run(ResearchRequest(
        question="Does Apple's FY2025 filing deny a demand decline?",
        ticker="AAPL",
        as_of=tools.as_of,
        language="en",
    ), tools)

    assert answer.answer
    assert any(
        claim.get("verification") == "deterministic_full_scan" for claim in answer.answer
    )
    assert any(t["stage"] == "negative_scan_fallback" for t in answer.trace)
    assert answer.status == "answered"


@pytest.mark.parametrize(
    "question",
    [
        "Does the FY2025 filing deny a demand decline?",
        "Did the company state there was no material supply-chain impact?",
        "What regulatory and demand risks does Apple disclose?",
    ],
)
def test_source_excerpt_or_fact_alone_cannot_complete_narrative_questions(question):
    from filingcopilot.semantic_boundaries import narrative_completeness_errors

    assert narrative_completeness_errors(
        [{"kind": "fact"}], question
    )
    assert narrative_completeness_errors(
        [{"kind": "excerpt"}], question
    )
    assert not narrative_completeness_errors(
        [{"kind": "narrative"}], question
    )


@pytest.mark.parametrize("question,statement,source", [
    ("Does the filing deny a demand decline?",
     "The capacity risk is inconsistent with a denial of any demand decline.",
     "If demand for added capacity does not materialize, costs may hurt financial performance."),
    ("公司是否披露没有重大供应链影响？",
     "未来中断风险与没有重大供应链影响的说法不一致。",
     "Supply chain disruptions could have a material adverse effect on operations."),
    ("Does the filing deny a demand decline?",
     "The warning that demand could decline is the opposite of a denial.",
     "A decline in demand could materially affect our results."),
    ("公司是否披露没有重大供应链影响？",
     "已披露的供应链风险与没有重大影响的说法相矛盾。",
     "Supply chain disruptions could have a material adverse effect on operations."),
    ("Did the company state there was no material supply-chain impact?",
     "Supply-chain volatility affected results, refuting the proposed statement.",
     "Supply chain volatility had direct and indirect effects that are difficult to isolate and quantify."),
])
def test_risk_or_unquantified_effect_cannot_prove_a_negative_proposition_false(tools, question, statement, source):
    accepted, errors = check(tools, statement, source, question)
    assert not accepted and any("counterevidence" in e for e in errors)


@pytest.mark.parametrize("question,statement,source", [
    ("Does the filing deny a demand decline?",
     "The company disclosed reduced demand, contradicting the proposed absence of a decline.",
     "We have experienced reduced demand during product transitions."),
    ("Did the company state there was no material supply-chain impact?",
     "The disclosed material supply-chain impact contradicts the proposed absence of such impact.",
     "Supply chain disruptions had a material adverse impact on operations during the year."),
    ("Did the company state there was no material supply-chain impact?",
     "The cited excerpts do not establish materiality; a risk warning cannot refute the proposed statement.",
     "Supply chain disruptions could have a material adverse effect on operations."),
    ("公司是否披露没有重大供应链影响？",
     "所引摘录只披露了可能的重大风险，不能据此反驳没有重大影响的说法。",
     "Supply chain disruptions could have a material adverse effect on operations."),
])
def test_direct_counterexamples_and_uncertainty_remain_reviewable(tools, question, statement, source):
    accepted, errors = check(tools, statement, source, question)
    assert accepted and not errors


def test_gap_notes_cannot_bypass_document_scope_checks(tools):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest
    from filingcopilot.settings import Settings

    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    unsafe = "The filing does not disclose actual supply-chain effects."

    class Gateway:
        def complete(self, messages, schemas, review=False):
            if review:
                context = json.loads(messages[-1]["content"])
                return {"message": {"content": json.dumps({
                    "verdicts": [{"index": c["index"], "supported": True, "reason": "Risk warning is in the passage."}
                                 for c in context["claims"]],
                    "gap_verdicts": [{"index": 0, "required_missing": True, "reason": "Keep limitation."}],
                })}, "model": "reviewer"}
            return {"message": {"tool_calls": [{"id": "submit", "type": "function", "function": {
                "name": "submit_answer", "arguments": json.dumps({"claims": [{
                    "kind": "narrative", "evidence_ids": ["p1"],
                    "statement": "Supply disruptions could adversely affect operations.",
                }], "gaps": [unsafe]}),
            }}]}, "model": "author"}

    settings = Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1, min_model_interval=0)
    answer = ResearchAgent(settings, Gateway()).run(ResearchRequest(
        question="What risks does Apple disclose?", ticker="AAPL", as_of=tools.as_of, language="en",
    ), tools)
    assert answer.answer and answer.status == "partial"
    assert unsafe not in answer.gaps
    assert any("document-wide absence" in gap for gap in answer.gaps)
    assert any(t["stage"] == "gap_scope_limited" for t in answer.trace)


def test_scope_guard_does_not_duplicate_an_existing_bounded_gap(tools):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest
    from filingcopilot.settings import Settings

    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    unsafe = "The filing does not disclose actual supply-chain effects."
    bounded = (
        "The reviewed passages do not contain an explicit denial; whether the "
        "unreviewed remainder does is unresolved, and a filing-wide absence is not established."
    )

    class Gateway:
        def complete(self, messages, schemas, review=False):
            if review:
                context = json.loads(messages[-1]["content"])
                return {"message": {"content": json.dumps({
                    "verdicts": [{"index": c["index"], "supported": True, "reason": "Risk warning is in the passage."}
                                 for c in context["claims"]],
                    "gap_verdicts": [{"index": 0, "required_missing": True, "reason": "Keep limitation."}],
                })}, "model": "reviewer"}
            return {"message": {"tool_calls": [{"id": "submit", "type": "function", "function": {
                "name": "submit_answer", "arguments": json.dumps({"claims": [{
                    "kind": "narrative", "evidence_ids": ["p1"],
                    "statement": "Supply disruptions could adversely affect operations.",
                }], "gaps": [unsafe, bounded]}),
            }}]}, "model": "author"}

    settings = Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1, min_model_interval=0)
    answer = ResearchAgent(settings, Gateway()).run(ResearchRequest(
        question="What risks does Apple disclose?", ticker="AAPL", as_of=tools.as_of, language="en",
    ), tools)
    assert unsafe not in answer.gaps
    assert bounded in answer.gaps
    assert sum("document-wide" in gap or "filing-wide" in gap or "整份" in gap for gap in answer.gaps) == 1


@pytest.mark.parametrize("gap", [
    "所检索段落中没有可直接引用的原文语句，该命题在未检索部分是否存在仍未解决，也不等同于已确认整份申报不存在此类表述。",
    "The reviewed passages cannot establish the proposition; a filing-wide absence is not established.",
    "Selected excerpts are not exhaustive.",
])
def test_scope_gap_present_recognizes_bounded_limitations(gap):
    from filingcopilot.semantic_boundaries import scope_gap_present
    assert scope_gap_present([gap])


@pytest.mark.parametrize("gap", [
    "The requested proposition remains unresolved: review found no direct evidence.",
    "部分内容未通过证据核验，已停止或移除相关结论。",
])
def test_scope_gap_present_ignores_repair_process_notes(gap):
    from filingcopilot.semantic_boundaries import scope_gap_present
    assert not scope_gap_present([gap])


@pytest.mark.parametrize("statement,unresolved", [
    ("The cited passages describe future risks; the proposition is left unresolved by this evidence.", True),
    ("所引摘录仅描述未来风险；是否明确否认需求下降仍无法确定。", True),
    ("The company explicitly denied a demand decline in the cited passage.", False),
    ("The company disclosed a demand decline. The severity of future risks remains unresolved.", False),
    ("The proposition is disclosed rather than left unresolved.", False),
    ("The requested conclusion is not unresolved: the company explicitly denied a demand decline.", False),
    ("该命题并非尚未解决：公司明确否认需求下降。", False),
])
def test_unresolved_requested_proposition_cannot_be_marked_complete(tools, statement, unresolved):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest
    from filingcopilot.settings import Settings

    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["p1"]["text"] = statement

    class Gateway:
        def complete(self, messages, schemas, review=False):
            if review:
                return {"message": {"content": json.dumps({"verdicts": [
                    {"index": 0, "supported": True, "reason": "Matches source"},
                ]})}, "model": "reviewer"}
            return {"message": {"tool_calls": [{"id": "submit", "type": "function", "function": {
                "name": "submit_answer", "arguments": json.dumps({"claims": [{
                    "kind": "narrative", "evidence_ids": ["p1"], "statement": statement,
                }], "gaps": []}),
            }}]}, "model": "author"}

    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1,
                                    min_model_interval=0), Gateway()).run(ResearchRequest(
        question="Does the filing deny a demand decline?", ticker="AAPL", as_of=tools.as_of,
        language="en",
    ), tools)
    assert answer.answer, "Retain correctly qualified observations"
    assert answer.status == ("partial" if unresolved else "answered")
    if unresolved:
        assert any("unresolved" in gap for gap in answer.gaps)
@pytest.mark.parametrize("statement,conflict", [
    ("申报未提供针对整体需求水平变化方向的单独量化披露。", True),
    ("公司没有给出整体需求变化的量化指标。", True),
    ("申报未提供对某一具体期间公司整体需求是否下降的统一否定或确认。", True),
    ("所引段落未提供对某一具体期间公司整体需求是否下降的统一否定或确认。", False),
    ("所引段落未提供针对整体需求水平变化方向的单独量化披露。", False),
    ("公司明确披露供应链事件没有重大影响。", False),
])
def test_missing_quantification_needs_the_actual_reviewed_scope(statement, conflict):
    from filingcopilot.semantic_boundaries import document_scope_conflict
    assert bool(document_scope_conflict(statement)) is conflict


@pytest.mark.parametrize("statement", [
    "Higher net sales are consistent with no aggregate demand decline having been recorded.",
    "Revenue growth indicates no decline in overall demand.",
    "The company grew without a demand decline.",
    "收入增长与总体需求没有下降相一致。",
    "收入增长说明没有出现总体需求下降。",
])
def test_revenue_growth_does_not_establish_demand_nonoccurrence(tools, statement):
    accepted, errors = check(tools, statement, "Consolidated net sales increased year over year.",
                             "Does the filing deny a demand decline?")
    assert not accepted and any("nonoccurrence" in error for error in errors)


@pytest.mark.parametrize("statement,source", [
    ("Revenue growth cannot establish that demand has not declined.", "Net sales increased."),
    ("收入增长不能证明需求没有下降。", "Net sales increased."),
    ("The source states there was no decline in overall demand.",
     "There was no decline in overall demand during the reporting period."),
    ("该段落明确表示本期需求没有下降。", "本期需求没有下降。"),
])
def test_demand_nonoccurrence_keeps_direct_sources_and_uncertainty(tools, statement, source):
    accepted, errors = check(tools, statement, source, "Does the filing deny a demand decline?")
    assert accepted, errors


@pytest.mark.parametrize("statement", [
    "没有重大供应链影响的说法与已披露的实际影响方向相悖。",
    "所引原文不支持没有重大供应链影响的说法；披露方向相反，指向已发生的中断。",
    "Actual supply-chain harm points in the opposite direction to no material impact.",
    "Observed supply-chain effects are contrary to an immaterial impact.",
])
def test_adverse_effects_do_not_oppose_a_nonmateriality_proposition(tools, statement):
    accepted, errors = check(
        tools,
        statement,
        "Supply disruptions adversely affected product availability.",
        "公司是否披露没有重大供应链影响？",
    )
    assert not accepted and any("counterevidence" in error for error in errors)


@pytest.mark.parametrize("statement", [
    "发生影响不能证明与没有重大供应链影响的结论方向相反。",
    "Actual effects cannot establish a conclusion contrary to no material supply-chain impact.",
])
def test_adverse_effects_uncertainty_is_not_refutation(tools, statement):
    accepted, errors = check(tools, statement, "Supply disruptions adversely affected product availability.",
                             "公司是否披露没有重大供应链影响？")
    assert accepted, errors


@pytest.mark.parametrize("statement", [
    "所引片段未包含竞争、网络安全等类别的具体披露，因此无法判断这些类别在年报中的具体表述。",
    "上述均为申报列示的条件性风险，所引片段未说明这些风险已实际造成重大损失。",
    "就所引原文而言，申报并未明确作出该披露。",
    "In the reviewed passages, the filing does not establish the materiality of the effect.",
    "In the quoted excerpts, the filing does not quantify any realized loss.",
])
def test_explicit_excerpt_scope_is_not_a_document_absence_claim(statement):
    from filingcopilot.semantic_boundaries import document_scope_conflict
    assert document_scope_conflict(statement) is None


@pytest.mark.parametrize("statement,question,source", [
    ("所引段落没有明确否认需求下降；相反，段落只是描述需求可能下降的风险。",
     "公司是否否认需求下降？", "If demand declines, revenue may fall."),
    ("所引段落未披露没有重大供应链影响；相反，原文描述供应短缺影响销售。",
     "公司是否披露没有重大供应链影响？", "Supply shortages affected sales."),
    ("这属于对可能情形的风险描述，并非对需求未下降的否认。",
     "公司是否否认需求下降？", "If demand declines, revenue may fall."),
    ("这是针对特定业务的需求增长描述，而非对企业整体需求未下降的明确否认。",
     "公司是否否认需求下降？", "Demand for AI services increased."),
    ("This is not a confirmation that demand has not declined.",
     "Does the filing deny a demand decline?", "Demand for AI services increased."),
    ("The risk note is not an explicit denial of no demand decline.",
     "Does the filing deny a demand decline?", "If demand declines, revenue may fall."),
])
def test_discourse_contrast_and_denial_discussion_are_not_counterclaims(tools, statement, question, source):
    accepted, errors = check(tools, statement, source, question)
    assert accepted, errors


def test_denial_discussion_does_not_hide_a_separate_nonoccurrence_claim(tools):
    accepted, errors = check(
        tools,
        "这并非对需求未下降的否认。整体需求没有下降。",
        "Demand for AI services increased.",
        "公司是否否认需求下降？",
    )
    assert not accepted and any("nonoccurrence" in error for error in errors)
