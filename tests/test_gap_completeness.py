"""Removing a redundant author note must not bypass answer completeness."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission


def test_existing_verifier_detects_missing_metric_when_no_gap_exempts_completeness(tools):
    revenue = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    draft = Submission(claims=[Claim(kind="fact", evidence_ids=[revenue["id"]])])
    question = "What were Apple's FY2025 revenue and net income?"
    _, errors = verify_submission(draft, tools, question, focus_ticker="AAPL")
    assert any("net_income" in error for error in errors)
    draft.gaps = ["No segment breakdown is supplied."]
    _, errors = verify_submission(draft, tools, question, focus_ticker="AAPL")
    assert not errors


@pytest.mark.parametrize("question,missing", [
    ("What were Apple's FY2025 revenue and net income? Explain its supply risk.", "net_income"),
    ("What were Apple's FY2025 and FY2024 revenues? Explain its supply risk.", "FY2024"),
    ("What was Apple's FY2025 revenue growth? Explain its supply risk.", "growth"),
    ("What were Apple's FY2025 revenue and gross margin? Explain its supply risk.", "gross_margin"),
    ("What were Apple and Microsoft's FY2025 revenues? Explain Apple's supply risk.", "MSFT"),
])
def test_retiring_unrequested_gap_rechecks_requested_numeric_coverage(tools, question, missing):
    revenue = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    gap = "No segment-by-segment revenue breakdown is supplied."

    class Gateway:
        calls = 0

        def complete(self, messages, schemas, review=False):
            self.calls += 1
            if not review:
                payload = {"claims": [
                    {"kind": "fact", "evidence_ids": [revenue["id"]]},
                    {"kind": "narrative", "evidence_ids": ["p1"],
                     "statement": "The company discloses supply-disruption risks."},
                ], "gaps": [gap]}
                return {"message": {"tool_calls": [{"id": "submit", "type": "function",
                        "function": {"name": "submit_answer", "arguments": json.dumps(payload)}}]},
                        "model": "author"}
            # All supplied claims are supported, and the segment breakdown is
            # genuinely unrequested. Neither decision establishes completeness.
            payload = {"verdicts": [{"index": 1, "supported": True, "reason": "Source supports the risk."}],
                       "gap_verdicts": [{"index": 0, "required_missing": False,
                                         "reason": "The user did not request segment detail."}]}
            return {"message": {"content": json.dumps(payload)}, "model": "judge"}

    gateway = Gateway()
    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off",
                                   min_model_interval=0, max_rounds=1), gateway).run(
        ResearchRequest(question=question, ticker="AAPL", language="en", as_of=tools.as_of), tools)
    assert answer.status == "partial", "Supported claims and irrelevant notes cannot complete missing fields"
    assert answer.answer and gap not in answer.gaps
    assert any(missing in finding for finding in answer.gaps)
    assert any(missing in finding for step in answer.trace if step["stage"] == "verify"
               for finding in step["findings"])
    assert gateway.calls == 2, "Coverage checks must not repeat paid semantic review"


@pytest.mark.parametrize("include_growth,missing_income", [(True, False), (False, False), (False, True)])
def test_complete_draft_can_drop_an_unrequested_gap_without_repeating_review(tools, include_growth, missing_income):
    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    growth = tools.calculate("growth", current["id"], prior["id"])["evidence"]
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)

    class Gateway:
        calls = 0

        def complete(self, messages, schemas, review=False):
            self.calls += 1
            if review:
                return {"model": "judge", "message": {"content": json.dumps({
                    "verdicts": [{"index": 2 if include_growth else 1, "supported": True,
                                  "reason": "Source supports the risk."}],
                    "gap_verdicts": [{"index": 0, "required_missing": False,
                                      "reason": "Segment detail is outside the question."}],
                })}}
            return {"model": "author", "message": {"tool_calls": [{"id": "submit", "type": "function",
                    "function": {"name": "submit_answer", "arguments": json.dumps({"claims": [
                        {"kind": "fact", "evidence_ids": [current["id"]]},
                        *([{"kind": "calculation", "evidence_ids": [growth["id"]]}] if include_growth else []),
                        {"kind": "narrative", "evidence_ids": ["p1"],
                         "statement": "Supply disruptions could adversely affect operations."},
                    ], "gaps": ["No segment-by-segment revenue breakdown is supplied."]})}}]}}

    gateway = Gateway()
    question = ("What were Apple's FY2025 revenue growth and net income? Explain its supply risk."
                if missing_income else "What was Apple's FY2025 revenue growth? Explain its supply risk.")
    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off",
                                   min_model_interval=0, max_rounds=1), gateway).run(
        ResearchRequest(question=question,
                        ticker="AAPL", language="en", as_of=tools.as_of), tools)
    if missing_income:
        assert answer.status == "partial" and any("net_income" in gap for gap in answer.gaps)
    else:
        assert answer.status == "answered" and not answer.gaps
    assert not any("revenue" in gap and "requires a calculate" in gap for gap in answer.gaps)
    assert len(answer.answer) == 3 and gateway.calls == 2
