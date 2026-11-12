"""Supported sentences can still leave the user's requested explanation missing."""

import json

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.settings import Settings


@pytest.mark.parametrize("complete,independent", [(True, True), (False, True), (True, False)])
def test_final_coverage_adjudicates_unrequested_gap_without_source_leakage(tools, complete, independent):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    gap = "The cited passage does not quantify the probability of the disclosed supply risk."
    contexts = []
    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            coverage = context["claims"][0]["kind"] == "answer_coverage"
            contexts.append(context)
            payload = {"verdicts": [{"index": c["index"], "supported": complete if coverage else True,
                         "reason": "The question asks what the risk is, not its probability."} for c in context["claims"]]}
            if not coverage:
                payload["gap_verdicts"] = [{"index": 0, "required_missing": True, "description_supported": True,
                                            "reason": "Source reviewer incorrectly demanded a probability."}]
            return {"model": "reviewer", "review_independence": "independent" if independent else "unknown",
                    "message": {"content": json.dumps(payload)}}
    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                    statement="Supply disruptions could adversely affect operations.")], gaps=[gap]),
                 tools=tools, question="What supply risk is disclosed?", focus_ticker="AAPL", rounds=1,
                 repairs=2, messages=[], verified=[], errors=[], trace=[], tool_count=0,
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    coverage = next(c for c in contexts if c["claims"][0]["kind"] == "answer_coverage")
    assert coverage.get("declared_gaps") == [gap], "Final coverage must actually judge the disputed omission"
    assert not coverage.get("evidence"), "Gap discussion must not reintroduce source leakage"
    assert (gap not in result["submission"].gaps) is (complete and independent)


def test_repair_feedback_exposes_available_uncited_calculation_ids(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.get_financial_fact("AAPL", "gross_profit", 2025)
    tools.get_financial_fact("AAPL", "revenue", 2025)
    calculation = tools.calculate("ratio", "gross25", "rev25")["evidence"]
    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": c["index"], "supported": c["kind"] != "answer_coverage", "reason": "Missing explanation."}
                for c in context["claims"]]})}}
    state = dict(submission=Submission(claims=[Claim(kind="analysis", evidence_ids=["p1"],
                    statement="Gross margin increased.")]), tools=tools, question="Why did FY2025 gross margin change?",
                 focus_ticker="AAPL", rounds=1, repairs=0, messages=[], verified=[], errors=[], trace=[], tool_count=0,
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    feedback = json.loads(result["messages"][-1]["content"])
    assert calculation["id"] in {c["id"] for c in feedback.get("available_calculations", [])}
    assert not result["verified"], "Making premises available is not approving a rejected claim"


@pytest.mark.parametrize("already_observed", [True, False])
def test_gap_search_reports_whether_it_found_new_evidence(tools, monkeypatch, already_observed):
    source = tools.corpus.evidence("p1", tools.as_of)
    if already_observed:
        tools.ledger["p1"] = source
    else:
        tools.ledger.pop("p1", None)

    def search(**kwargs):
        tools.ledger["p1"] = source
        return {"hits": [{"passage": source}]}

    monkeypatch.setattr(tools, "search_passages", search)
    state = dict(tools=tools, focus_ticker="AAPL", question="Did the company deny a supply impact?",
                 trace=[], tool_count=0, usage={})
    result = ResearchAgent._retrieve_gap_evidence(state, ["No explicit materiality assessment found."])
    assert bool(result) is (not already_observed)
    assert state["tool_count"] == 1
    assert state["trace"][-1]["selected_ids"] == ["p1"]


def test_final_coverage_cannot_read_omitted_source_content(tools):
    """The source's regulatory disclosure cannot fill a missing answer paragraph."""
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of), "text": (
        "Supply disruptions could harm operations. Regulatory restrictions may limit sales."
    )}
    contexts = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            contexts.append(context)
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": c["index"], "supported": c["kind"] != "answer_coverage" or bool(context.get("evidence")),
                 "reason": "Source is supported." if c["kind"] != "answer_coverage" else "Missing the regulatory risk in the delivered answer."}
                for c in context["claims"]
            ]})}}

    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                 statement="Supply disruptions could harm operations.")]), tools=tools,
                 question="What supply and regulatory risks are disclosed?", focus_ticker="AAPL",
                 rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    coverage = [c for c in contexts if any(x["kind"] == "answer_coverage" for x in c["claims"])]
    assert coverage and all(not c.get("evidence") for c in coverage)
    assert all("Regulatory restrictions" not in json.dumps(c) for c in coverage)
    assert result["verified"] and result["answer_coverage_key"] is None
    assert any("coverage" in e for e in result["errors"])


@pytest.mark.parametrize("question,claim,missing,repairs,action,searched", [
    ("Why did FY2025 operating margin improve?", "Gross margin declined and operating expenses increased.",
     "Explain how operating expenses grew more slowly than revenue and offset the gross-margin decline.",
     2, "end", False),
    ("What supply and regulatory risks are disclosed?", "Supply disruptions could harm operations.",
     "The requested regulatory risk is missing from the answer.", 0, "reason", True),
])
def test_whole_answer_coverage_is_checked_even_without_author_gaps(
        tools, monkeypatch, question, claim, missing, repairs, action, searched
):
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of), "text": (
        "Supply disruptions could harm operations. Regulatory restrictions may limit sales. "
        "Gross margin declined. Revenue grew faster than operating expenses, improving operating margin."
    )}
    searches, contexts = [], []
    def search(**kwargs):
        searches.append(kwargs)
        return {"hits": [{"passage": tools.ledger["p1"]}]}
    monkeypatch.setattr(tools, "search_passages", search)

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert review
            context = json.loads(messages[-1]["content"])
            contexts.append(context)
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": c["index"], "supported": c["kind"] != "answer_coverage",
                 "reason": missing if c["kind"] == "answer_coverage" else "This sentence is supported."}
                for c in context["claims"]
            ]})}}

    state = dict(submission=Submission(claims=[Claim(kind="narrative", statement=claim, evidence_ids=["p1"])]),
                 tools=tools, question=question, focus_ticker="AAPL", rounds=1, repairs=repairs,
                 messages=[], verified=[], errors=[], trace=[], tool_count=0, action="",
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0))
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=4), Gateway()).verify(state)
    assert any(c["kind"] == "answer_coverage" for c in contexts[-1]["claims"])
    assert len(result["verified"]) == 1, "Incomplete coverage must not erase a supported sentence"
    assert result["action"] == action
    assert any("coverage" in error.casefold() for error in result["errors"])
    assert bool(searches) is searched
    if searched:
        assert searches[0]["ticker"] == "AAPL"
    assert result["submission"].gaps, "A required omission stays visible if repair ends here"


def test_coverage_probe_is_not_delivered_as_a_financial_claim(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    contexts = []
    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            contexts.append(context)
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": c["index"], "supported": True, "reason": "The requested risk is covered."}
                for c in context["claims"]
            ]})}}
    state = dict(submission=Submission(claims=[Claim(kind="narrative", statement="Supply disruptions could harm operations.", evidence_ids=["p1"])]),
                 tools=tools, question="What supply risk is disclosed?", focus_ticker="AAPL", rounds=1, repairs=0,
                 messages=[], verified=[], errors=[], trace=[], tool_count=0, action="",
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0))
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    assert any(c["kind"] == "answer_coverage" for c in contexts[-1]["claims"])
    assert [c["kind"] for c in result["verified"]] == ["narrative"]
    assert len(result["submission"].claims) == 1
    assert not result["errors"] and not result["submission"].gaps


@pytest.mark.parametrize("change", ["unchanged", "answer", "source", "question"])
def test_coverage_cache_binds_question_answer_and_actual_sources(tools, change):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            calls.append(context)
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": c["index"], "supported": True, "reason": "Requested supply risk is covered."}
                for c in context["claims"]
            ]})}}

    submission = Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                                        statement="Supply disruptions could harm operations.")])
    state = dict(submission=submission, tools=tools, question="What supply risks are disclosed?",
                 focus_ticker="AAPL", rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    agent = ResearchAgent(Settings(home=tools.corpus.home), Gateway())
    first = agent.verify(state)
    if change == "answer":
        first["submission"] = submission.model_copy(deep=True)
        first["submission"].claims[0].statement = "Supply interruptions could hurt operations."
    elif change == "source":
        tools.ledger["p1"]["text"] += " Supply interruptions can delay production."
    elif change == "question":
        first["question"] = "What operational supply risk is disclosed?"
    result = agent.verify(first)
    assert len(calls) == (2 if change == "unchanged" else 4)
    assert result["answer_coverage_key"] == agent._coverage_key(result["verified"])


@pytest.mark.parametrize("mode", ["missing", "invalid", "outage"])
def test_invalid_or_unavailable_coverage_cannot_approve_an_answer(tools, mode):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)

    class Gateway:
        def complete(self, messages, schemas, review=False):
            if mode == "outage":
                raise RuntimeError("review unavailable")
            verdicts = [{"index": 0, "supported": True, "reason": "Source matches."}]
            if mode == "invalid":
                verdicts.append({"index": 1, "supported": "true", "reason": "Wrong type."})
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": verdicts})}}

    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                 statement="Supply disruptions could harm operations.")]), tools=tools,
                 question="What supply risks are disclosed?", focus_ticker="AAPL", rounds=1, repairs=2,
                 messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    assert result["review_failed"] and result["errors"]
    assert result["answer_coverage_key"] is None


@pytest.mark.parametrize("coverage_supported,independent,expected", [
    (True, True, "answered"), (False, True, "partial"), (True, False, "partial"),
])
def test_optional_withheld_clause_needs_exact_independent_coverage(tools, coverage_supported, independent, expected):
    class Gateway:
        def complete(self, messages, schemas, review=False):
            if review:
                context = json.loads(messages[-1]["content"])
                return {"model": "reviewer", "review_independence": "independent" if independent else "not_independent",
                        "message": {"content": json.dumps({"verdicts": [
                            {"index": c["index"],
                             "supported": coverage_supported if c["kind"] == "answer_coverage" else c["index"] == 0,
                             "reason": "Risk covered" if coverage_supported else "Missing requested risk"}
                            for c in context["claims"]
                        ]})}}
            return {"model": "author", "message": {"tool_calls": [{"id": "draft", "function": {
                "name": "submit_answer", "arguments": json.dumps({"claims": [
                    {"kind": "narrative", "evidence_ids": ["p1"], "statement": "Supply disruptions could harm operations."},
                    {"kind": "narrative", "evidence_ids": ["p1"], "statement": "Management considers this the largest risk."},
                ], "gaps": []})}}]}}
    answer = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), Gateway()).run(
        ResearchRequest(question="What supply risks are disclosed?", ticker="AAPL",
                        as_of=tools.as_of, language="en"), tools)
    assert len(answer.answer) == 1
    assert answer.status == expected
    assert all("largest" not in c["text"] for c in answer.answer)


def test_repair_possible_skips_cause_coverage_review(tools):
    """A cause/risk pass that will repair must not spend a serial coverage call."""
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    contexts = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            contexts.append(context)
            return {
                "model": "reviewer",
                "review_independence": "independent",
                "message": {"content": json.dumps({"verdicts": [
                    {"index": c["index"], "supported": False,
                     "reason": "The cited passage does not explain the net change."}
                    for c in context["claims"]
                ]})},
            }

    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                 statement="Gross margin declined and operating expenses increased.")]), tools=tools,
                 question="Why did FY2025 gross margin change?", focus_ticker="AAPL",
                 rounds=1, repairs=0, messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)

    assert contexts, "The claim still needs its source review"
    assert all(c["claims"][0]["kind"] != "answer_coverage" for c in contexts), (
        "The stale coverage pass must be skipped when a repair is pending"
    )
    assert result["answer_coverage_review"] is None
    assert result["answer_coverage_key"] is None
    assert result["action"] == "reason"
    assert any(
        step.get("stage") == "coverage_review_skipped" and step.get("reason") == "repair_pending"
        for step in result["trace"]
    )


def test_exhausted_repair_budget_runs_final_coverage_review(tools):
    """The last pass still makes the coverage call that governs delivery."""
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    contexts = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            contexts.append(context)
            coverage = context["claims"][0]["kind"] == "answer_coverage"
            return {
                "model": "reviewer",
                "review_independence": "independent",
                "message": {"content": json.dumps({"verdicts": [
                    {"index": c["index"], "supported": coverage or c["index"] == 0,
                     "reason": "Coverage judged on the delivered answer."
                     if coverage else "Only the first claim is supported."}
                    for c in context["claims"]
                ]})},
            }

    state = dict(submission=Submission(claims=[
                     Claim(kind="narrative", evidence_ids=["p1"],
                           statement="Supply disruptions could harm operations."),
                     Claim(kind="narrative", evidence_ids=["p1"],
                           statement="Management expects no impact at all."),
                 ]), tools=tools,
                 question="What supply risks are disclosed?", focus_ticker="AAPL",
                 rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)

    assert any(c["claims"][0]["kind"] == "answer_coverage" for c in contexts)
    assert not any(step.get("stage") == "coverage_review_skipped" for step in result["trace"])
    assert result["action"] == "end"
    assert result["answer_coverage_key"] is not None


@pytest.mark.parametrize("question", [
    "What was Apple's FY2025 revenue, and why did it change?",
    "What were Apple's FY2025 revenue and net income and why did they change?",
    "苹果FY2025的收入是多少，并解释其变化原因？",
])
def test_coverage_approval_does_not_waive_explicit_numeric_requirement(tools, question):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    tools.get_financial_fact("AAPL", "revenue", 2025)

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[-1]["content"])
            return {"model": "reviewer", "message": {"content": json.dumps({"verdicts": [
                {"index": c["index"], "supported": True, "reason": "Review approves prose."}
                for c in context["claims"]
            ]})}}

    state = dict(submission=Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"],
                 statement="Supply disruptions could harm operations.")]), tools=tools,
                 question=question, focus_ticker="AAPL",
                 rounds=1, repairs=2, messages=[], verified=[], errors=[], trace=[],
                 usage=dict(model_calls=0, prompt_tokens=0, completion_tokens=0, total_tokens=0), action="")
    result = ResearchAgent(Settings(home=tools.corpus.home), Gateway()).verify(state)
    assert result["verified"]
    assert any("numeric" in error.casefold() or "revenue" in error.casefold() for error in result["errors"])
