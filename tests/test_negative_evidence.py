"""Full-filing negative evidence supports a bounded "no explicit statement" answer."""

from filingcopilot.fundamentals import narrative_source_context
from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission


def negative_record(**overrides):
    record = {
        "id": "negative-test",
        "kind": "negative_evidence",
        "ticker": "AAPL",
        "filing_id": "AAPL:old",
        "section": "Full-filing keyword scan",
        "text": (
            "Full-filing keyword scan of 500 indexed passages from 10-K "
            "(period end 2025-09-27): the explicit-negative pattern matched 0 passages. "
            "Slot hits: {\"否定\": 0, \"反证\": 2}."
        ),
        "source_url": "https://www.sec.gov/Archives/old.htm",
        "form": "10-K",
        "filed": "2025-10-31",
        "period_end": "2025-09-27",
        "scope": "full_filing_keyword_scan",
        "passages_scanned": 500,
        "slot_hits": {"否定": 0, "反证": 2},
        "matching_passages": ["p1"],
        "proposition": "苹果 FY2025 是否披露没有重大供应链影响？",
        "support_limit": "Exact keyword pattern only; paraphrases are not assessed.",
        "filings": [
            {
                "ticker": "AAPL",
                "form": "10-K",
                "filed": "2025-10-31",
                "period_end": "2025-09-27",
            }
        ],
    }
    record.update(overrides)
    return record


def test_negation_context_exposes_a_full_scan_negative_record(tools):
    context, _ = narrative_source_context(
        tools, "AAPL", "苹果 FY2025 是否披露没有重大供应链影响？请核对原文。"
    )

    assert context["full_filing_scan"] is True
    assert context["scan_scope"]["passages_scanned"] == context["full_filing_passage_count"]
    assert "否定" in context["scan_scope"]["slot_hits"]
    negative = [
        item for item in context["evidence"] if item.get("kind") == "negative_evidence"
    ]
    assert len(negative) == 1
    record = negative[0]
    assert record["scope"] == "full_filing_keyword_scan"
    assert record["passages_scanned"] == context["full_filing_passage_count"]
    assert record["slot_hits"] == context["scan_scope"]["slot_hits"]


def test_bounded_negative_claim_is_accepted(tools):
    tools.ledger["negative-test"] = negative_record()
    claim = Claim(
        kind="narrative",
        evidence_ids=["negative-test"],
        statement=(
            "No explicit statement that the company had no material supply-chain impact "
            "appears in the indexed full-filing keyword scan; the filing describes supply "
            "disruptions as a conditional risk."
        ),
    )

    accepted, errors = verify_submission(
        Submission(claims=[claim]),
        tools,
        "苹果 FY2025 是否披露没有重大供应链影响？请核对原文。",
        focus_ticker="AAPL",
    )

    assert not errors
    assert accepted and accepted[0]["verification"] == "deterministic_full_scan"


def test_absolute_document_absence_is_rejected(tools):
    tools.ledger["negative-test"] = negative_record()
    claim = Claim(
        kind="narrative",
        evidence_ids=["negative-test"],
        statement="The entire filing never mentions supply-chain impact.",
    )

    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果 FY2025 是否披露没有重大供应链影响？"
    )

    assert not accepted
    assert any("bounded" in error for error in errors)


def test_nonzero_explicit_negative_slot_is_rejected(tools):
    tools.ledger["negative-test"] = negative_record(slot_hits={"否定": 1, "反证": 2})
    claim = Claim(
        kind="narrative",
        evidence_ids=["negative-test"],
        statement="No explicit statement appears in the indexed full-filing keyword scan.",
    )

    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果 FY2025 是否披露没有重大供应链影响？"
    )

    assert not accepted
    assert any("matching passage" in error or "nonzero explicit-negative" in error for error in errors)


def test_nonzero_negative_hits_with_a_cited_matching_passage_are_reviewable(tools):
    tools.ledger["negative-test"] = negative_record(slot_hits={"否定": 2, "反证": 2})
    tools.ledger["p1"] = tools.corpus.evidence("p1", tools.as_of)
    claim = Claim(
        kind="narrative",
        evidence_ids=["negative-test", "p1"],
        statement=(
            "The explicit-negative passages concern COVID-19 and cybersecurity, not the "
            "requested supply-chain proposition; no matching statement appears in the scan."
        ),
    )

    def reviewer(question, narratives):
        return {
            "verdicts": [
                {"index": claim["index"], "supported": True, "reason": "mismatch verified"}
                for claim in narratives
            ]
        }

    accepted, errors = verify_submission(
        Submission(claims=[claim]),
        tools,
        "Did the filing state that there was no material supply-chain impact?",
        semantic_review=reviewer,
        focus_ticker="AAPL",
    )

    assert not errors
    assert accepted and accepted[0]["verification"] == "negative_scan_mismatch"


def test_nonzero_negative_hits_without_a_matching_passage_are_rejected(tools):
    tools.ledger["negative-test"] = negative_record(slot_hits={"否定": 2, "反证": 2})
    claim = Claim(
        kind="narrative",
        evidence_ids=["negative-test"],
        statement=(
            "The explicit-negative passages concern a different subject; no matching statement."
        ),
    )

    accepted, errors = verify_submission(
        Submission(claims=[claim]),
        tools,
        "Did the filing state that there was no material supply-chain impact?",
        focus_ticker="AAPL",
    )

    assert not accepted
    assert any("matching passage" in error for error in errors)


def test_negative_claim_must_stay_qualitative(tools):
    tools.ledger["negative-test"] = negative_record()
    claim = Claim(
        kind="narrative",
        evidence_ids=["negative-test"],
        statement=(
            "No explicit statement appears in the indexed full-filing keyword scan; "
            "supply-chain costs rose 5%."
        ),
    )

    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果 FY2025 是否披露没有重大供应链影响？"
    )

    assert not accepted
    assert any("qualitative" in error for error in errors)


def test_scan_scope_caveats_do_not_block_once_the_bounded_question_is_answered():
    from filingcopilot.semantic_boundaries import nonblocking_gap

    question = "Did TSMC state that there was no material supply-chain impact in FY2023?"
    gaps = [
        "The keyword scan covers exact patterns only; paraphrased wording was not assessed.",
        "No quantitative demand measure was established, so the actual impact remains unresolved.",
        "The requested proposition remains unresolved: review found no direct evidence.",
        "所检索证据尚不足以确认部分细节；不能据此认定整份文件没有相关披露。",
    ]
    for gap in gaps:
        assert nonblocking_gap(question, gap, full_scan=True, negative_resolved=True)
        assert not nonblocking_gap(question, gap, full_scan=True)


def test_negative_evidence_prompt_is_scoped_to_negation():
    from filingcopilot.agent import _research_system_prompt
    from filingcopilot.models import ResearchRequest

    negation = _research_system_prompt(
        ResearchRequest(
            question="Did Apple deny a demand decline?",
            ticker="AAPL",
            language="en",
        ),
        has_refresh=False,
        full_scan=True,
    )
    risk = _research_system_prompt(
        ResearchRequest(
            question="What risks did Apple disclose?",
            ticker="AAPL",
            language="en",
        ),
        has_refresh=False,
        full_scan=True,
    )

    assert "you may answer 'No' when that bounded scan result is present" in negation
    assert "zero matches do not prove" not in negation
    assert "zero matches do not prove" in risk
    assert "you may answer 'No' when that bounded scan result is present" not in risk


def test_coverage_supported_scope_caveats_do_not_block():
    from filingcopilot.semantic_boundaries import nonblocking_gap

    gaps = [
        "The answer explains the drivers qualitatively but does not quantify each segment's contribution.",
        "The cited excerpt continues beyond the displayed text, so the remainder was not reviewed.",
        "Supply-side concentration was not includible within the five-claim limit.",
        "所检索证据尚不足以确认部分细节；不能据此认定整份文件没有相关披露。",
    ]
    for gap in gaps:
        assert nonblocking_gap(
            "Which disclosed drivers explain the change?",
            gap,
            coverage_supported=True,
        )
        assert not nonblocking_gap("Which disclosed drivers explain the change?", gap)


def test_negative_resolved_waives_actual_outcome_and_budget_notes():
    from filingcopilot.semantic_boundaries import nonblocking_gap

    question = "Does the FY2025 filing deny a demand decline?"
    gaps = [
        "The cited conditional language does not establish an actual FY2025 demand outcome.",
        "The cited passages do not establish an actual demand direction for FY2025.",
        "The cited passages do not state whether the impact actually occurred or was material.",
        "The filing contains no disclosed assessment that actual FY2023 supply-chain impact was material.",
        "A required point remains unresolved; the draft's explanation of the limitation could not be verified.",
        "模型未在研究预算内提交完整回答；已保留通过核验的内容。",
    ]
    for gap in gaps:
        assert nonblocking_gap(question, gap, negative_resolved=True)
        assert not nonblocking_gap(question, gap)


def test_negative_scan_fallback_adds_a_bounded_claim_for_excerpt_only_answers():
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest

    state = {
        "verified": [
            {
                "index": 0,
                "kind": "excerpt",
                "text": "A source excerpt.",
                "evidence_ids": ["p1"],
                "verification": "exact_source_excerpt",
            }
        ],
        "trace": [],
    }
    context = {"full_filing_scan": True, "evidence": [negative_record()]}
    request = ResearchRequest(
        question="苹果 FY2025 是否披露没有重大供应链影响？",
        ticker="AAPL",
        language="zh",
    )

    ResearchAgent._add_negative_scan_fallback(state, request, context)

    assert any(
        claim.get("verification") == "deterministic_full_scan" for claim in state["verified"]
    )
    assert any(item.get("stage") == "negative_scan_fallback" for item in state["trace"])


def test_negative_scan_fallback_stays_out_when_the_model_already_synthesized():
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest

    state = {
        "verified": [
            {
                "index": 0,
                "kind": "narrative",
                "text": "The filing treats this as a conditional risk.",
                "evidence_ids": ["p1"],
                "verification": "semantic_review",
            }
        ],
        "trace": [],
    }
    context = {"full_filing_scan": True, "evidence": [negative_record()]}
    request = ResearchRequest(
        question="Did Apple deny a demand decline?",
        ticker="AAPL",
        language="en",
    )

    ResearchAgent._add_negative_scan_fallback(state, request, context)

    assert not any(
        claim.get("verification") == "deterministic_full_scan" for claim in state["verified"]
    )


def test_negative_scan_fallback_skips_nonzero_explicit_negative_hits():
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest

    state = {"verified": [], "trace": []}
    context = {
        "full_filing_scan": True,
        "evidence": [negative_record(slot_hits={"否定": 2, "反证": 2})],
    }
    request = ResearchRequest(
        question="Did TSMC deny a material supply-chain impact?",
        ticker="TSM",
        language="en",
    )

    ResearchAgent._add_negative_scan_fallback(state, request, context)

    assert state["verified"] == []

