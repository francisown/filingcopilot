"""Qualitative synthesis must not mistake ordinary words or source labels for amounts."""

import pytest

from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission


@pytest.mark.parametrize("statement", [
    "应分别核对各项变化，避免归结为统一成因。",
    "单一成本项目不足以解释所有变化。",
    "这一成果的持续性仍需观察。",
    "Form 20-F discusses supply risks without establishing whether they materialized.",
    "20-F/A中的风险描述不能证明事件已经发生。",
    "The COVID-19 discussion describes uncertainty rather than proving that disruption occurred.",
])
def test_nonfinancial_words_and_bound_identifiers_reach_semantic_review(tools, statement):
    item = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["p1"] = {**item, "filing": {**item["filing"], "form": "20-F/A"},
                          "text": "COVID-19 may disrupt supply. Operating costs have multiple components."}
    reviewed = []

    def review(question, claims):
        reviewed.extend(claims)
        return {"verdicts": [{"index": c["index"], "supported": True,
                              "reason": "Controlled source review."} for c in claims]}

    answer, errors = verify_submission(Submission(claims=[Claim(
        kind="analysis", evidence_ids=["p1"], statement=statement,
    )]), tools, "What can be inferred from these disclosures?", review)
    assert answer and reviewed, errors


@pytest.mark.parametrize("statement", [
    "费用增长一成。", "三成收入来自该业务。", "收入为一万元。",
    "这一成果使利润增长三成。", "Revenue rose COVID-19%.",
    "COVID-99 caused the change.", "Form 99-F establishes the finding.",
])
def test_financial_amounts_and_unbound_identifiers_are_not_stripped(tools, statement):
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of),
                          "text": "COVID-19 may disrupt supply."}
    answer, errors = verify_submission(Submission(claims=[Claim(
        kind="analysis", evidence_ids=["p1"], statement=statement,
    )]), tools, "What can be inferred from these disclosures?")
    assert not answer
    assert any("numeric" in e for e in errors)
