"""Only a valid independent review may retire an unnecessary model-authored gap."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import ResearchRequest
from filingcopilot.semantic_boundaries import nonblocking_gap
from filingcopilot.settings import Settings


def test_methodological_caveats_stay_visible_without_downgrading_answer_status():
    assert nonblocking_gap(
        "What regulatory, demand and concentration risks did Apple disclose?",
        "The filing describes most risks qualitatively and does not quantify probability or magnitude.",
    )
    assert nonblocking_gap(
        "Did Apple state that there was no material supply-chain impact?",
        "Only selected excerpts were searched; the entire filing was not reviewed.",
    )
    assert nonblocking_gap(
        "Which disclosed drivers explain Apple's FY2022 growth?",
        "The optional Mac-specific product detail was not retrieved.",
    )
    assert not nonblocking_gap(
        "What regulatory, demand and concentration risks did Apple disclose?",
        "No customer concentration disclosure was located, so the requested concentration risk remains unresolved.",
    )
    assert nonblocking_gap(
        "Does Apple's FY2024 filing explicitly deny a demand decline?",
        "A document-wide absence was not established in the searched passages.",
        full_scan=True,
    )
    assert nonblocking_gap(
        "What regulatory, demand and concentration risks did Apple disclose?",
        "The concentration percentage is not available as a typed structured metric.",
    )


@pytest.mark.parametrize(
    ("question", "gap"),
    [
        (
            "英伟达 FY2022 是否披露没有重大供应链影响？请核对原文。",
            "The reviewed passages were retrieved by focused keyword searches over the FY2022 10-K; "
            "they establish what the cited sections say but do not prove that no other passage "
            "anywhere in the filing contains a supply-chain non-materiality statement.",
        ),
        (
            "苹果 FY2024 是否明确否认需求下降？",
            "No explicit denial of a demand decline was found in the searched FY2024 excerpts, which "
            "cover retrieved risk-factor and MD&A passages rather than an exhaustive review of the "
            "entire filing, so passages outside the searched excerpts remain unassessed.",
        ),
        (
            "Does the FY2025 filing deny a demand decline for Amazon?",
            "No explicit statement denying a demand decline was located in the searched excerpts of "
            "the annual report (management's discussion and risk factors excerpts), so a document-wide "
            "absence of such wording was not established by these searches.",
        ),
    ],
)
def test_scope_caveat_is_retired_only_after_a_real_full_filing_scan(question, gap):
    """The same sentence is blocking without a scan and a caveat after one."""
    assert not nonblocking_gap(question, gap, full_scan=False)
    assert nonblocking_gap(question, gap, full_scan=True)


def test_unresolved_content_still_blocks_after_a_full_filing_scan():
    """A scan covers the search space, not an unanswered question inside it."""
    assert not nonblocking_gap(
        "Did TSMC state that there was no material supply-chain impact in FY2023?",
        "Some details remain unresolved in the retrieved evidence; "
        "document-wide absence has not been established.",
        full_scan=True,
    )


def test_only_pure_claim_rejections_count_as_a_quality_event():
    from filingcopilot.semantic_boundaries import withheld_only

    # A rejected sentence, with coverage intact, must not downgrade the answer.
    assert withheld_only(["claim 2: overstated direction"])
    assert withheld_only(["claim 0: citation was not observed", "claim 3: fiscal year mismatch"])
    # A missing requested item is a coverage failure and stays blocking.
    assert not withheld_only(["Requested margin net_margin for AAPL FY2025 requires a calculate tool result"])
    assert not withheld_only(["Narrative verifier failed; descriptive claims were withheld"])
    # Mixed means something requested is genuinely uncovered.
    assert not withheld_only(["claim 1: overstated", "Requested metric missing for AAPL FY2025: revenue"])
    assert not withheld_only([])


@pytest.mark.parametrize("case", ["resolved", "required", "malformed", "non_independent", "claim_rejected"])
def test_gap_scope_review_uses_existing_review_without_weakening_claims(tools, case):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    gap = "The proposed absence of supply-disruption risk was not found in the cited paragraph."

    class Gateway:
        calls = 0

        def complete(self, messages, schemas, review=False):
            self.calls += 1
            if not review:
                return {"message": {"role": "assistant", "tool_calls": [{"id": "submit", "type": "function", "function": {
                    "name": "submit_answer", "arguments": json.dumps({"claims": [{"kind": "narrative", "evidence_ids": ["p1"],
                    "statement": "The company warns that supply disruptions could adversely affect operations."}], "gaps": [gap]})}}]}, "model": "author"}
            payload = {"verdicts": [{"index": 0, "supported": case != "claim_rejected", "reason": "fixture"}],
                       "gap_verdicts": [{"index": 99 if case == "malformed" else 0,
                                         "required_missing": case == "required",
                                         "reason": "The cited warning directly addresses the proposed absence of risk."}]}
            result = {"message": {"content": json.dumps(payload)}, "model": "judge"}
            if case == "non_independent":
                result["review_independence"] = "not_independent"
            return result

    gateway = Gateway()
    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off", min_model_interval=0, max_rounds=1), gateway).run(
        ResearchRequest(question="Does Apple disclose no supply-disruption risk? Check the cited text.", ticker="AAPL",
                        language="en", as_of=tools.as_of), tools)
    if case == "resolved":
        assert answer.status == "answered" and not answer.gaps
        assert any(t["stage"] == "gap_scope_review" for t in answer.trace)
    else:
        assert gap in answer.gaps
    if case == "claim_rejected":
        assert not answer.answer
    else:
        assert answer.answer, "Malformed optional gap review must not erase valid claim review"
    assert gateway.calls == 2


def test_missing_concentration_number_is_not_a_missing_risk_answer():
    """Apple discloses receivables and geographic concentration, not a revenue share."""
    question = "What regulatory, demand, and concentration risks did Apple disclose for FY2023?"
    assert nonblocking_gap(
        question,
        "No numeric single-customer revenue concentration was found in the scanned "
        "FY2023 filing excerpts beyond the receivables disclosures cited.",
    )
    assert nonblocking_gap(
        question,
        "The retrieved excerpts do not include a numeric single-customer revenue "
        "concentration figure for FY2024; the disclosed evidence concerns China-based suppliers.",
    )
    assert nonblocking_gap(
        question,
        "该10-K未见对单一或少数主要客户或供应商收入占比的量化披露，"
        "本回答的集中度风险仅基于地域与业务分部结构。",
    )
    # The category itself never being retrieved is still a real gap.
    assert not nonblocking_gap(
        "Which risks should an analyst track?",
        "No passage was retrieved for the requested risk category; the answer remains incomplete.",
    )


def test_bounded_risk_brief_does_not_downgrade_verified_claims_for_extra_categories():
    assert nonblocking_gap(
        "Which risks should an analyst track in TSMC's FY2025 filing?",
        "Also disclosed but not includible within the five-claim limit: supplier and talent risk.",
    )
