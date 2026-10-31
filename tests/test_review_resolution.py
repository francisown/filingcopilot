"""Review completeness independently from author wording and cached approvals."""
import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.settings import Settings


@pytest.mark.parametrize("outcome,basis,status", [
    ("unresolved", "", "partial"),
    ("supported", "Supply disruptions had no material impact.", "answered"),
    ("refuted", "Supply disruptions had a material impact.", "answered"),
    ("refuted", "An invented quotation.", "partial"),
    ("refuted", "", "partial"),
])
def test_reviewed_resolution_controls_completeness_without_keyword_matching(tools, outcome, basis, status):
    source = basis if status == "answered" else "Supply disruptions could affect operations."
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["p1"]["text"] = source
    # The graph refreshes observed evidence from the corpus during preparation.
    with tools.corpus.connect() as db:
        payload = json.loads(db.execute("SELECT payload FROM passages WHERE id='p1'").fetchone()[0])
        payload["text"] = source
        db.execute("UPDATE passages SET payload=? WHERE id='p1'", (json.dumps(payload),))
        db.execute("UPDATE passages_fts SET text=? WHERE id='p1'", (source,))

    class Gateway:
        def complete(self, messages, schemas, review=False):
            if review:
                payload = {"claim_0_source_supported": True, "claim_0_inference_supported": True,
                           "claim_0_document_absence": False,
                           "claim_0_proposition_relation": "observation",
                           "claim_0_reason": "Scoped observation is accurate.",
                           "answer_resolution": outcome, "answer_resolution_reason": "Evidence scope assessment.",
                           "answer_resolution_evidence_id": "p1", "answer_resolution_quote": basis}
            else:
                payload = {"claims": [{"kind": "narrative", "evidence_ids": ["p1"],
                                       "statement": "The source discusses operational effects."}], "gaps": []}
            return {"model": "reviewer" if review else "author", "message": {"tool_calls": [
                {"id": "call", "function": {"name": "submit_review" if review else "submit_answer",
                                              "arguments": json.dumps(payload)}}]}}

    answer = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1, min_model_interval=0), Gateway()).run(
        ResearchRequest(question="Did the company report no material supply-chain impact?", ticker="AAPL",
                        as_of=tools.as_of, language="en"), tools)
    assert answer.answer and answer.status == status
    if status == "partial":
        assert any("unresolved" in gap for gap in answer.gaps)


@pytest.mark.parametrize("required_missing", [True, False])
def test_new_gap_is_reviewed_when_all_claim_approvals_are_cached(tools, required_missing):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    claim = Claim(kind="narrative", evidence_ids=["p1"], statement="Supply disruptions could harm operations.")

    class Gateway:
        calls = 0

        def complete(self, messages, schemas, review=False):
            assert review
            self.calls += 1
            context = json.loads(messages[1]["content"])
            payload = {"verdicts": [{"index": c["index"], "supported": True, "reason": "source matches"}
                                    for c in context["claims"]]}
            if context.get("gap_candidates"):
                payload["gap_verdicts"] = [{"index": 0, "required_missing": required_missing,
                                           "reason": "Check whether the requested evidence is still absent."}]
            return {"message": {"content": json.dumps(payload)}, "model": "reviewer"}

    gateway = Gateway()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), gateway)
    state = dict(submission=Submission(claims=[claim]), tools=tools, question="What risks are disclosed?",
                 focus_ticker="AAPL", rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    first = agent.verify(state)
    gap = "Evidence for the requested category is missing." if required_missing else "No requested point remains unresolved."
    second = agent.verify({**first, "submission": Submission(claims=[claim], gaps=[gap])})
    assert gateway.calls == 2
    assert second["submission"].gaps == ([gap] if required_missing else [])


@pytest.mark.parametrize("outcome,basis", [
    ("unresolved", ""),
    ("supported", "Supply disruptions could adversely affect operations."),
    ("refuted", "An invented quotation."),
])
def test_claimed_refutation_cannot_override_an_incompatible_resolution(tools, outcome, basis):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    conclusion = "That proposition is the opposite of the source."

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            payload = {"answer_resolution": outcome, "answer_resolution_quote": basis,
                       "answer_resolution_evidence_id": "p1", "answer_resolution_reason": "Scope assessment"}
            for index, relation in ((0, "refutes"), (1, "observation")):
                payload.update({f"claim_{index}_source_supported": True,
                                f"claim_{index}_inference_supported": True,
                                f"claim_{index}_document_absence": False,
                                f"claim_{index}_proposition_relation": relation,
                                f"claim_{index}_reason": "A deliberately inconsistent reviewer approval"})
            return {"message": {"content": json.dumps(payload)}, "model": "reviewer"}

    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway())
    state = dict(submission=Submission(claims=[
        Claim(kind="narrative", evidence_ids=["p1"], statement=conclusion),
        Claim(kind="narrative", evidence_ids=["p1"], statement="Supply disruptions could adversely affect operations."),
    ]), tools=tools, question="Did the company report no material supply-chain impact?",
        focus_ticker="AAPL", rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = agent.verify(state)
    assert [c["index"] for c in result["verified"]] == [1]
    assert any("conflicts with the reviewed evidence resolution" in error for error in result["errors"])
