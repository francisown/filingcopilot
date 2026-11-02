"""Review completeness independently from author wording and cached approvals."""
import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.settings import Settings


@pytest.mark.parametrize("question,anchor,expected", [
    ("According to the FY2023 annual report, was there no material risk as of the report date?",
     "As of the date of this annual report, there is no material risk from cybersecurity threats.", True),
    ("Was there no material cybersecurity impact in FY2023?",
     "As of the date of this annual report, there is no material risk from cybersecurity threats.", False),
    ("Did demand fall in FY2023?", "Demand fell during fiscal year 2023.", True),
    ("Did demand fall in FY2023?", "Demand has fallen in past periods.", False),
])
def test_resolution_period_uses_event_or_explicit_report_date_scope(question, anchor, expected):
    from filingcopilot.semantic_boundaries import resolution_period_supported
    assert resolution_period_supported(question, anchor, anchor, {2023}) is expected
    assert not resolution_period_supported(question, anchor, "Unrelated text.", {2023})


def test_withheld_requested_explanation_is_not_reported_complete(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    class Gateway:
        def complete(self, messages, schemas, review=False):
            if review:
                indexes = json.loads(messages[-1]["content"])["required_indexes"]
                return {"model": "judge", "message": {"content": json.dumps({"verdicts": [
                    {"index": index, "supported": index == 0, "reason": "Supply supported; requested regulatory risk missing"}
                    for index in indexes
                ]})}}
            return {"model": "author", "message": {"tool_calls": [{"id": "draft", "function": {
                "name": "submit_answer", "arguments": json.dumps({"claims": [
                    {"kind": "narrative", "evidence_ids": ["p1"], "statement": "Supply disruptions could harm operations."},
                    {"kind": "narrative", "evidence_ids": ["p1"], "statement": "Regulatory restrictions threaten operations."},
                ], "gaps": []})}}]}}
    answer = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway()).run(
        ResearchRequest(question="What supply and regulatory risks are disclosed?", ticker="AAPL",
                        as_of=tools.as_of, language="en"), tools)
    assert len(answer.answer) == 1
    assert answer.status == "partial", "Valid surviving prose is not proof of complete coverage"
    assert all(not any("\u4e00" <= char <= "\u9fff" for char in gap) for gap in answer.gaps), answer.gaps


def test_causal_finalization_uses_the_question_shape_contract(tools):
    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert "For this cause question" in messages[-1]["content"]
            return {"model": "author", "message": {"tool_calls": [{"id": "draft", "function": {
                "name": "submit_answer", "arguments": "{}"}}]}}
    state = dict(question="Why did revenue grow?", tools=tools, trace=[], rounds=3, repairs=0,
                 tool_count=0, errors=[], messages=[], usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0),
                 narrative_context={"answerability": "supported_candidate", "evidence_slots": [{"id": "p1"}]})
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=8), Gateway()).reason(state)
    assert result["action"] == "tools", result.get("errors")


def test_format_failure_does_not_replay_malformed_submission_to_model(tools):
    agent = ResearchAgent(Settings(home=tools.corpus.home), object())
    state = dict(question="Why did revenue grow?", tools=tools, trace=[], rounds=4, tool_count=0,
                 seen=set(), usage={}, messages=[{"role": "assistant", "tool_calls": [{
                     "id": "broken", "function": {"name": "submit_answer", "arguments": '{"claims"'}}]}])
    result = agent.execute(state)
    assert result["submission"] is None and result["action"] == "reason"
    assert json.loads(result["messages"][0]["tool_calls"][0]["function"]["arguments"]) == {}
    feedback = json.loads(result["messages"][1]["content"])
    assert "claims" in feedback["repair_instruction"] and "evidence_ids" in feedback["repair_instruction"]
    assert result["trace"][0]["result"]["error"], "Keep the original error for diagnosis"


@pytest.mark.parametrize("submitted_kind,source_kind", [("fact", "calculation"), ("calculation", "fact")])
def test_typed_submission_kind_is_taken_from_observed_single_result(tools, submitted_kind, source_kind):
    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    item = current if source_kind == "fact" else tools.calculate("growth", current["id"], prior["id"])["evidence"]
    agent = ResearchAgent(Settings(home=tools.corpus.home), object())
    original = {"kind": submitted_kind, "statement": "", "evidence_ids": [item["id"]]}
    state = dict(question="What was revenue growth?", tools=tools, trace=[], rounds=1, tool_count=0,
                 seen=set(), usage={}, messages=[{"role": "assistant", "tool_calls": [{
                     "id": "typed", "function": {"name": "submit_answer",
                     "arguments": json.dumps({"claims": [original], "gaps": []})}}]}])
    result = agent.execute(state)
    claim = result["submission"].claims[0]
    assert claim.kind == source_kind
    assert claim.statement == "" and claim.evidence_ids == [item["id"]]
    assert result["trace"][-1]["arguments"]["claims"][0]["kind"] == submitted_kind, "Keep original model input in trace"


@pytest.mark.parametrize("complete,kind", [(True, "narrative"), (False, "narrative"), (True, "excerpt")])
def test_submission_binds_already_observed_complete_paragraph(tools, complete, kind):
    source = tools.corpus.evidence("p1", tools.as_of)
    for i in range(3 if complete else 2):
        tools.ledger[f"part-{i}"] = {**source, "id": f"part-{i}",
                                    "locator": f"narrative block 8, chunk {i}/3; exact text retained"}
    tools.ledger["other-filing"] = {**tools.ledger["part-0"], "id": "other-filing", "filing_id": "different"}
    original = {"kind": kind, "statement": source["text"], "evidence_ids": ["part-1"]}
    state = dict(question="Explain the disclosed risk.", tools=tools, trace=[], rounds=1, tool_count=0,
                 seen=set(), usage={}, messages=[{"role": "assistant", "tool_calls": [{
                     "id": "parts", "function": {"name": "submit_answer",
                     "arguments": json.dumps({"claims": [original], "gaps": []})}}]}])
    result = ResearchAgent(Settings(home=tools.corpus.home), object()).execute(state)
    claim = result["submission"].claims[0]
    assert claim.evidence_ids == (["part-1", "part-0", "part-2"] if complete and kind == "narrative" else ["part-1"])
    assert claim.statement == original["statement"]
    assert result["trace"][-1]["arguments"]["claims"][0] == original


@pytest.mark.parametrize("claim", [
    {"kind": "fact", "statement": "Invented amount", "evidence_ids": ["calc"]},
    {"kind": "fact", "statement": "", "evidence_ids": ["calc", "fact"]},
    {"kind": "fact", "statement": "", "evidence_ids": ["calc-unknown"]},
    {"kind": "fact", "statement": "", "evidence_ids": ["row"]},
    {"kind": "narrative", "statement": "Claimed fact", "evidence_ids": ["fact"]},
])
def test_submission_binding_does_not_guess_content_or_unobserved_types(claim):
    from filingcopilot.submission_protocol import bind_observed_submission
    ledger = {"calc": {"kind": "calculation"}, "fact": {"kind": "fact"}, "row": {"kind": "statement_fact"}}
    submitted = Submission.model_validate({"claims": [claim]})
    assert bind_observed_submission(submitted, ledger) == submitted


def test_observed_paragraph_binding_preserves_evidence_limit(tools):
    from filingcopilot.submission_protocol import bind_observed_submission
    source = tools.corpus.evidence("p1", tools.as_of)
    for i in range(9):
        tools.ledger[str(i)] = {**source, "id": str(i), "locator": f"narrative block 1, chunk {i}/9; exact text retained"}
    submitted = Submission(claims=[Claim(kind="narrative", statement="A claim", evidence_ids=["1"])])
    assert bind_observed_submission(submitted, tools.ledger) == submitted


@pytest.mark.parametrize("scope_supported,quote,status", [
    (False, "In past years, demand for older products declined when buyers anticipated new releases.", "partial"),
    (True, "In past years, demand for older products declined when buyers anticipated new releases.", "partial"),
    (True, "In past years, demand for older products declined when buyers anticipated", "partial"),
    (True, "company-wide demand declined materially.", "partial"),
    (True, "During fiscal 2025, company-wide demand declined materially.", "answered"),
])
def test_resolution_requires_matching_scope_and_a_complete_evidence_sentence(tools, scope_supported, quote, status):
    source = ("In past years, demand for older products declined when buyers anticipated new releases. "
              "During fiscal 2025, company-wide demand declined materially.")
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of), "text": source}

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            payload = {
                "claim_0_source_supported": True, "claim_0_inference_supported": True,
                "claim_0_document_absence": False, "claim_0_scope_supported": True,
                "claim_0_scope_check": "The observation preserves the source's period and product scope.",
                "claim_0_proposition_relation": "observation", "claim_0_reason": "Scoped observation.",
                "answer_resolution_evidence_id": "p1", "answer_resolution_quote": quote,
                "answer_resolution_scope_check": (
                    "FY2025 company-wide demand is explicitly addressed." if scope_supported else
                    "Unspecified past years and older products do not establish FY2025 company-wide demand."
                ),
                "answer_resolution_scope_supported": scope_supported,
                "answer_resolution": "refuted", "answer_resolution_reason": "Demand declined.",
            }
            allowed = schemas[0]["function"]["parameters"]["properties"]
            payload = {key: value for key, value in payload.items() if key in allowed}
            return {"message": {"content": json.dumps(payload)}, "model": "reviewer"}

    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway())
    state = dict(submission=Submission(claims=[Claim(
        kind="narrative", evidence_ids=["p1"], statement=source,
    )]), tools=tools, question="Did the company deny a material company-wide demand decline in FY2025?",
        focus_ticker="AAPL", rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = agent.verify(state)
    assert result["verified"], result["errors"]
    assert result["answer_resolution_review"]["outcome"] == ("refuted" if status == "answered" else "unresolved")


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
                           "claim_0_scope_supported": True, "claim_0_scope_check": "Scopes match",
                           "claim_0_proposition_relation": "observation",
                           "claim_0_reason": "Scoped observation is accurate.",
                           "answer_resolution": outcome, "answer_resolution_reason": "Evidence scope assessment.",
                           "answer_resolution_scope_supported": True,
                           "answer_resolution_scope_check": "The disclosure matches the question's scope.",
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
            payload = {"verdicts": [{"index": c["index"],
                                    "supported": not (c["kind"] == "answer_coverage" and required_missing and context.get("declared_gaps")),
                                    "reason": "Requested category is missing." if context.get("declared_gaps") and required_missing else "source matches"}
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
    assert gateway.calls == 4  # Source/gap review and isolated delivered-answer coverage.
    if required_missing:
        assert gap in second["submission"].gaps
        assert second["answer_coverage_review"]["supported"] is False
    else:
        assert second["submission"].gaps == []


@pytest.mark.parametrize("repairs", [0, 2])
@pytest.mark.parametrize("required_missing", [True, False])
def test_false_operational_gap_is_withheld_without_waiving_missing_evidence(tools, repairs, required_missing):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    gap = "The requested impact remains unknown because no search tool was available."

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[1]["content"])
            if context.get("evidence"):
                assert context["research_operations"]["completed_tools"] == ["search_passages"]
            return {"model": "reviewer", "message": {"content": json.dumps({
                "verdicts": [{"index": c["index"], "supported": c["kind"] != "answer_coverage" or not required_missing,
                              "reason": "Required impact missing" if required_missing else "Requested risk covered"}
                             for c in context["claims"]],
                **({"gap_verdicts": [{"index": 0, "required_missing": required_missing,
                                  "description_supported": False,
                                  "reason": "Search ran; the missing impact remains unresolved."}]}
                   if context.get("gap_candidates") else {}),
            })}}

    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=4), Gateway())
    state = dict(submission=Submission(claims=[Claim(
        kind="narrative", evidence_ids=["p1"], statement="Supply disruptions could harm operations.",
    )], gaps=[gap]), tools=tools, question="What supply risks and realized impacts are disclosed?",
        focus_ticker="AAPL", rounds=1, repairs=repairs, messages=[], verified=[], errors=[],
        trace=[{"stage": "tool", "tool": "search_passages", "elapsed_ms": 5}],
        usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = agent.verify(state)
    assert result["verified"], result["errors"]
    assert gap not in result["submission"].gaps
    assert bool(result["submission"].gaps) is required_missing
    assert any("gap 0" in error for error in result["errors"]) is required_missing
    assert any(t["stage"] == "gap_description_rejected" for t in result["trace"])
    assert result["action"] == ("reason" if repairs == 0 and required_missing else "end")


def test_gap_reference_is_available_to_reviewer_and_binds_scope_cache(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["gap-source"] = {**tools.ledger["p1"], "id": "gap-source", "text": "Only possible future effects are described."}
    contexts = []
    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[1]["content"])
            if context.get("evidence"):
                contexts.append(context["evidence"].get("gap-source"))
            return {"model": "reviewer", "message": {"content": json.dumps({
                "verdicts": [{"index": c["index"], "supported": True, "reason": "Scoped observation"}
                             for c in context["claims"]],
                "gap_verdicts": [{"index": 0, "required_missing": True, "description_supported": True,
                                  "reason": "The missing evidence is not resolved by gap-source."}],
            })}}
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway())
    submission = Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"], statement="Supply disruptions could harm operations.")],
                            gaps=["The gap-source passage does not establish realized material harm."])
    state = dict(submission=submission, tools=tools, question="What are the risks?", focus_ticker="AAPL",
                 rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[], tool_count=20,
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    first = agent.verify(state)
    assert contexts[0] and contexts[0]["text"] == tools.ledger["gap-source"]["text"]
    tools.ledger["gap-source"]["text"] = "A changed observed source also only describes future effects."
    agent.verify({**first, "submission": submission.model_copy(deep=True)})
    assert len(contexts) == 2
    assert contexts[1]["text"] == tools.ledger["gap-source"]["text"]


def test_repaired_gap_keeps_observed_sources_from_rejected_prior_claim(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.ledger["allocation"] = {**tools.ledger["p1"], "id": "allocation",
                                  "text": "Severance was recorded in payroll expenses."}
    contexts = []
    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[1]["content"])
            if context.get("evidence"):
                contexts.append(context)
            if context.get("gap_candidates"):
                assert context["evidence"]["allocation"]["text"] == tools.ledger["allocation"]["text"]
                assert "invented-id" not in context["evidence"]
            payload = {"verdicts": [
                {"index": c["index"], "supported": "allocation" not in c["evidence_ids"],
                 "reason": "The earlier allocation claim is rejected, not the observed source."}
                for c in context["claims"]
            ]}
            if context.get("gap_candidates"):
                payload["gap_verdicts"] = [{"index": 0, "required_missing": True,
                                            "description_supported": True,
                                            "reason": "Source supports the note but not a full reconciliation."}]
            return {"model": "reviewer", "message": {"content": json.dumps(payload)}}
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway())
    safe = Claim(kind="narrative", evidence_ids=["p1"], statement="Supply disruptions could harm operations.")
    state = dict(submission=Submission(claims=[safe, Claim(kind="narrative", evidence_ids=["allocation"],
                statement="Severance was recorded in other operating expenses.")]),
                 tools=tools, question="Explain the disclosed costs.", focus_ticker="AAPL", rounds=1,
                 repairs=2, messages=[], verified=[], errors=[], trace=[], tool_count=20,
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    first = agent.verify(state)
    assert len(first["verified"]) == 1
    revised = Submission(claims=[safe], gaps=["Payroll includes severance, but the full cost reconciliation remains unresolved."])
    second = agent.verify({**first, "submission": revised})
    assert not second["review_failed"], second["trace"]
    assert len(second["verified"]) == 1, "A prior source is context, not approval of its rejected claim"
    assert contexts[-1]["evidence"]["allocation"]["text"] == tools.ledger["allocation"]["text"]
    tools.ledger["allocation"]["text"] = "Severance remains in payroll; the source was refreshed."
    agent.verify({**second, "submission": revised.model_copy(deep=True)})
    assert len(contexts) == 3, "Changed retained source must invalidate the gap review cache"


@pytest.mark.parametrize("description_supported", [True, False])
def test_missing_point_uses_review_query_even_while_repairing_a_gap_note(tools, monkeypatch, description_supported):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    queries = []
    def search(**kwargs):
        queries.append(kwargs)
        return {"hits": [{"passage": {**tools.ledger["p1"], "id": "new-driver"}}]}
    monkeypatch.setattr(tools, "search_passages", search)
    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[1]["content"])
            return {"model": "reviewer", "message": {"content": json.dumps({
                "verdicts": [{"index": c["index"], "supported": c["kind"] != "answer_coverage",
                              "reason": "The requested expense driver is missing"}
                             for c in context["claims"]],
                **({"gap_verdicts": [{"index": 0, "required_missing": True,
                                  "description_supported": description_supported,
                                  "search_query": "infrastructure expenses increased depreciation",
                                  "reason": "The requested expense driver is still missing."}]}
                   if context.get("gap_candidates") else {}),
            })}}
    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"], statement="Supply disruptions could harm operations.")],
                                      gaps=["缺少基础设施费用增长的原因。"]),
                 tools=tools, question="解释 FY2025 的费用增长原因", focus_ticker="AAPL", rounds=1,
                 repairs=0, messages=[], verified=[], errors=[], trace=[], tool_count=0,
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=4), Gateway()).verify(state)
    assert len(queries) == 1
    assert queries[0]["query"] == "infrastructure expenses increased depreciation"
    assert queries[0]["ticker"] == "AAPL" and queries[0]["fiscal_year"] == 2025
    assert result["gap_evidence"]["evidence"] and result["action"] == "reason"
    assert result["verified"], "New evidence must not discard existing approved claims"


@pytest.mark.parametrize("outcome,basis", [
    ("unresolved", ""),
    ("supported", "Supply disruptions could adversely affect operations."),
    ("refuted", "An invented quotation."),
])
def test_claimed_refutation_cannot_override_an_incompatible_resolution(tools, outcome, basis):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    # Exercise resolution reconciliation after review, rather than the local
    # counterevidence wording gate that now rejects "opposite" before review.
    conclusion = "The proposed statement is false."

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            payload = {"answer_resolution": outcome, "answer_resolution_quote": basis,
                       "answer_resolution_scope_supported": True,
                       "answer_resolution_scope_check": "The disclosure matches the question's scope.",
                       "answer_resolution_evidence_id": "p1", "answer_resolution_reason": "Scope assessment"}
            for index, relation in ((0, "refutes"), (1, "observation")):
                payload.update({f"claim_{index}_source_supported": True,
                                f"claim_{index}_inference_supported": True,
                                f"claim_{index}_document_absence": False,
                                f"claim_{index}_scope_supported": True,
                                f"claim_{index}_scope_check": "Scopes match",
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
