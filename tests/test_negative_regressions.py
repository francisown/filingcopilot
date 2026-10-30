"""Regressions for observed downsides of the delivery and scope changes."""

import hashlib
import json

import pytest

from filingcopilot.annual_numbers import numeric_annual_question, reference_answer
from filingcopilot.audit import audit_package, create_package, digest
from filingcopilot.localization import language_scope
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.verification import verify_submission


@pytest.mark.parametrize("question", [
    "比较苹果 FY2025 与上一财年的收入增长，期间是否一致？",
    "Compare Apple's FY2025 revenue with the prior fiscal year using the same period basis.",
    "苹果 FY2025 收入变化的披露原因是什么？",
])
def test_requested_prior_year_fact_is_usable(tools, question):
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[prior["id"]])],
                   gaps=["The rest of the answer is not supplied by this fixture."]),
        tools, question, focus_ticker="AAPL",
    )
    assert accepted, errors


@pytest.mark.parametrize("question", [
    "比较苹果 FY2025 与上一财年的收入增长，期间是否一致？",
    "Compare Apple's FY2025 revenue with the prior fiscal year using the same period basis.",
    "苹果 FY2025 经营现金流同比是否可计算？",
    "Can Apple's FY2025 operating cash flow growth be calculated without mixing periods?",
    "苹果 FY2025 收入是多少，来源是什么？",
])
def test_source_metadata_and_period_arithmetic_do_not_need_a_model(tools, question):
    assert numeric_annual_question(question, tools.domain)


def test_prior_comparison_delivers_both_periods_and_growth(tools):
    answer = reference_answer(ResearchRequest(
        question="比较苹果 FY2025 与上一财年的收入增长，期间是否一致？",
        ticker="AAPL", as_of=tools.as_of, language="zh",
    ), tools, tools.domain)
    assert answer.status == "answered", answer.gaps
    assert {e.get("fiscal_year") for e in answer.evidence if e["kind"] == "fact"} == {2024, 2025}
    assert any(e.get("operation") == "growth" for e in answer.evidence)


def test_narrative_cut_at_tool_length_limit_is_not_sent_for_approval(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    text = ("Manufacturing partners can face supply disruptions. " * 30)[:998] + " a"
    assert len(text) == 1000

    def review(_question, _claims):
        pytest.fail("A visibly cut-off claim must be repaired before paid semantic review")

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"], statement=text)]),
        tools, "What are Apple's manufacturing risks?", semantic_review=review,
        focus_ticker="AAPL",
    )
    assert not accepted and any("incomplete" in error.lower() for error in errors)


@pytest.mark.parametrize("question", [
    "苹果 FY2025 收入是多少？",
    "苹果 FY2025 收入会计政策变化了吗？",
])
def test_unrequested_prior_year_stays_outside_amount_scope(tools, question):
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[prior["id"]])], gaps=["fixture"]),
        tools, question, focus_ticker="AAPL",
    )
    assert not accepted and any("fiscal year" in error for error in errors)


@pytest.mark.parametrize("question", [
    "苹果 FY2025 收入增长的原因是什么，期间是否一致？",
    "苹果 FY2025 收入是多少，收入来源和业务结构是什么？",
    "Compare Apple's FY2025 revenue accounting policy with the prior year.",
    "苹果 FY2025 收入增长是否可持续？",
])
def test_business_and_accounting_interpretation_still_need_analysis(tools, question):
    assert not numeric_annual_question(question, tools.domain)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_source_and_period_details_are_auditable_and_tamper_detected(tools, language):
    with language_scope(language):
        answer = reference_answer(ResearchRequest(
            question="Compare Apple's FY2025 revenue with the prior fiscal year using the same period basis.",
            ticker="AAPL", as_of=tools.as_of, language=language,
        ), tools, tools.domain)
    answer.language = language
    assert answer.status == "answered", answer.gaps
    assert all(c.get("rendering") == "annual_detail_v1" for c in answer.answer)
    assert "0000320193-25-000079" in answer.answer[0]["text"]
    calc = next(c for c in answer.answer if c["kind"] == "calculation")
    assert "2023-10-01" in calc["text"] and "2025-09-27" in calc["text"]
    package = create_package(answer, tools.domain, "AAPL")
    assert audit_package(package, corpus=tools.corpus)["status"] == "consistent"
    package["research"]["answer"][0]["text"] += " Altered source."
    body = {k: v for k, v in package.items() if k != "manifest"}
    package["manifest"]["payload_sha256"] = digest(body)
    report = audit_package(package, corpus=tools.corpus)
    assert report["status"] == "invalid"
    assert any(c["code"] == "numeric_text" and c["status"] == "fail" for c in report["checks"])


def test_complete_length_boundary_sentence_can_still_be_reviewed(tools):
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    text = "Supply disruptions can affect manufacturing. " * 23
    reviewed = []

    def review(question, claims):
        reviewed.extend(claims)
        return {"verdicts": [{"index": c["index"], "supported": True, "reason": "fixture"} for c in claims]}

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"], statement=text)]),
        tools, "What are Apple's manufacturing risks?", semantic_review=review, focus_ticker="AAPL",
    )
    assert accepted and reviewed and not errors


def test_causal_change_starts_with_bound_comparison_before_model(tools):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.settings import Settings

    class InspectContext:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[1]["content"])
            comparison = context.get("annual_comparison")
            assert comparison, "Comparison must be available before drafting or repairs"
            assert {e["fiscal_year"] for e in comparison["evidence"] if e["kind"] == "fact"} == {2024, 2025}
            assert any(e.get("operation") == "growth" for e in comparison["evidence"])
            return {"message": {"role": "assistant", "content": "No submission"}, "model": "fixture"}

    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1,
                                    max_tool_calls=6, min_model_interval=0), InspectContext()).run(
        ResearchRequest(question="苹果 FY2025 收入变化的披露原因是什么？", ticker="AAPL",
                        as_of=tools.as_of, language="zh"), tools,
    )
    assert any(t.get("stage") == "annual_comparison_preparation" for t in answer.trace)
    assert any(e.get("operation") == "growth" for e in answer.evidence)
    assert answer.usage["tool_calls"] <= 6


@pytest.mark.parametrize("prior_start,current_start,current_end,expected", [
    ("2023-10-01", "2024-09-29", "2025-09-27", True),
    ("2023-09-24", "2024-09-29", "2025-09-27", True),
    ("2023-10-01", "2024-10-01", "2025-12-27", False),
    ("2023-10-01", "2024-11-01", "2025-10-27", False),
    (None, None, "2025-09-27", False),
])
def test_period_comparison_preserves_real_windows(tools, prior_start, current_start, current_end, expected):
    from filingcopilot.annual_details import comparable_annual_periods
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    prior = {**prior, "start": prior_start, "end": "2024-09-28"}
    current = {**current, "start": current_start, "end": current_end}
    assert comparable_annual_periods(current, prior) is expected


def test_missing_comparative_input_remains_a_specific_gap(tools):
    with language_scope("en"):
        answer = reference_answer(ResearchRequest(
            question="Compare Apple's FY2025 gross profit with the prior year.",
            ticker="AAPL", as_of=tools.as_of, language="en",
        ), tools, tools.domain)
    assert answer.status == "partial"
    assert any("2024" in gap for gap in answer.gaps)
    assert not any(e.get("operation") == "growth" for e in answer.evidence)


def test_known_raw_filing_conflict_cannot_be_delivered_as_verified(tools):
    fact = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    cache = tools.corpus.home / "sec-cache"
    cache.mkdir()
    path = cache / (hashlib.sha256(fact["filing"]["source_url"].encode()).hexdigest() + ".cache")
    path.write_bytes(b"Different filing bytes; a matching number does not repair source binding.")
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[fact["id"]])]), tools,
        "Apple FY2025 revenue", focus_ticker="AAPL",
    )
    assert not accepted
    assert any("Cached filing bytes differ" in error for error in errors)
    assert path.read_bytes().startswith(b"Different filing bytes"), "Do not repair integrity by rewriting evidence"


def test_a_single_reviewer_failure_does_not_withhold_every_claim(tools):
    """One network hop must not decide that the whole answer is unverifiable."""
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    calls = {"n": 0}

    def flaky_review(question, claims):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient reviewer outage")
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "Approved on retry"}
                for c in claims
            ]
        }

    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="narrative",
                    statement="Manufacturing partners can face supply disruptions.",
                    evidence_ids=["p1"],
                )
            ]
        ),
        tools,
        "What are Apple's manufacturing risks?",
        semantic_review=flaky_review,
        focus_ticker="AAPL",
    )
    assert calls["n"] == 2
    assert accepted, errors
    assert not any("verifier failed" in error for error in errors)


def test_truncated_turn_asks_for_a_compact_submission():
    """A turn cut at the output cap must submit next time, not explore again."""
    from pathlib import Path

    from filingcopilot.agent_execution import ExecutionMixin
    from filingcopilot.settings import Settings

    class _Agent(ExecutionMixin):
        def __init__(self):
            self.settings = Settings(home=Path("unused-test-only"))

    state = {
        "messages": [{"role": "assistant", "tool_calls": [], "content": "a very long draft"}],
        "trace": [],
        "rounds": 1,
        "truncated_turn": True,
        "question": "Which risks matter?",
    }
    out = _Agent().execute(state)
    assert out["action"] == "reason"
    assert "truncated at the output limit" in out["messages"][-1]["content"]
    assert out.get("truncated_turn") is False


def test_empty_submission_is_nudged_before_abstention():
    """A provider's empty submit call must get a bounded repair turn."""
    from pathlib import Path

    from filingcopilot.agent_execution import ExecutionMixin
    from filingcopilot.settings import Settings

    class _Agent(ExecutionMixin):
        def __init__(self):
            self.settings = Settings(home=Path("unused-test-only"))

    state = {
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "empty-1",
                "function": {"name": "submit_answer", "arguments": '{"claims": [], "gaps": []}'},
            }],
        }],
        "trace": [],
        "question": "Which risks matter?",
        "rounds": 1,
        "tool_count": 0,
        "usage": {},
        "seen": set(),
        "tool_cache": {},
    }
    out = _Agent().execute(state)

    assert out["action"] == "reason"
    assert out["submission"] is None
    assert out["empty_submission_attempts"] == 1
    assert "Empty submission" in out["trace"][-1]["result"]["error"]


@pytest.mark.parametrize(
    "gap",
    ["占位", "placeholder", "TBD", "暂无", "待定", "…", "`N/A`"],
)
def test_placeholder_only_submission_is_nudged_before_abstention(gap):
    """A literal placeholder must not be treated as an intentional refusal."""
    from pathlib import Path

    from filingcopilot.agent_execution import ExecutionMixin
    from filingcopilot.settings import Settings

    class _Agent(ExecutionMixin):
        def __init__(self):
            self.settings = Settings(home=Path("unused-test-only"))

    state = {
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "placeholder-1",
                "function": {
                    "name": "submit_answer",
                    "arguments": json.dumps({"claims": [], "gaps": [gap]}),
                },
            }],
        }],
        "trace": [],
        "question": "Which risks matter?",
        "rounds": 1,
        "tool_count": 0,
        "usage": {},
        "seen": set(),
        "tool_cache": {},
    }
    out = _Agent().execute(state)

    assert out["action"] == "reason"
    assert out["submission"] is None
    assert out["empty_submission_attempts"] == 1
    assert "Empty submission" in out["trace"][-1]["result"]["error"]


@pytest.mark.parametrize("gap", ["没有披露该指标", "无法确认是否发生", "无重大影响的直接披露"])
def test_meaningful_evidence_boundary_is_not_filtered_as_placeholder(gap):
    """A real limitation must remain a limitation, not trigger empty retry."""
    from pathlib import Path

    from filingcopilot.agent_execution import ExecutionMixin
    from filingcopilot.settings import Settings

    class _Agent(ExecutionMixin):
        def __init__(self):
            self.settings = Settings(home=Path("unused-test-only"))

    state = {
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "boundary-1",
                "function": {
                    "name": "submit_answer",
                    "arguments": json.dumps({"claims": [], "gaps": [gap]}),
                },
            }],
        }],
        "trace": [],
        "question": "Which risks matter?",
        "rounds": 1,
        "tool_count": 0,
        "usage": {},
        "seen": set(),
        "tool_cache": {},
    }
    out = _Agent().execute(state)

    assert out["action"] == "verify"
    assert out["submission"].gaps == [gap]
    assert out.get("empty_submission_attempts", 0) == 0
