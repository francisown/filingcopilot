"""Only a valid independent review may retire an unnecessary model-authored gap."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import ResearchRequest
from filingcopilot.settings import Settings


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
