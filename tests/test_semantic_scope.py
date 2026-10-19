"""Semantic boundaries must survive an erroneous approval from a reviewer."""

import json

import pytest

from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission

FX_SOURCE = (
    "Weaker foreign currencies relative to the U.S. dollar can lead us to raise "
    "international prices, which may reduce demand."
)


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


def test_numeric_only_delivery_keeps_an_explicit_explanation_gap(tools):
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
        question="What risks does Apple disclose?", ticker="AAPL", as_of=tools.as_of, language="en",
    ), tools)
    assert answer.answer and answer.status == "partial"
    assert any("explanation is still missing" in gap for gap in answer.gaps)
    assert any(t["stage"] == "answer_coverage" for t in answer.trace)


@pytest.mark.parametrize("question,statement,source", [
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
                return {"message": {"content": json.dumps({
                    "verdicts": [{"index": 0, "supported": True, "reason": "Risk warning is in the passage."}],
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
