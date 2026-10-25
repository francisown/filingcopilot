"""Regression tests for the FinanceBench evaluation scripts.

These tests intentionally use synthetic pages: no PDF downloads, no model, no
network. They pin the routing decisions that the full-filing evaluator relies on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pdf_eval = load_script("financebench_pdf_eval", "evaluate_financebench_pdf.py")
page_eval = load_script("financebench_page_eval", "evaluate_financebench.py")


def test_dpo_routes_to_both_statements() -> None:
    intents, explicit = pdf_eval.question_intents(
        "What is FY2017 days payable outstanding (DPO)?"
    )
    assert explicit
    assert {"balance_sheet", "income"} <= intents


def test_segment_and_balance_sheet_intents() -> None:
    intents, explicit = pdf_eval.question_intents(
        "Which reporting segment had the highest net revenue?"
    )
    assert explicit
    assert "segment" in intents

    intents, explicit = pdf_eval.question_intents(
        "What is the quick ratio using current assets and current liabilities?"
    )
    assert explicit
    assert "balance_sheet" in intents


def test_topic_routes_for_qualitative_questions() -> None:
    topics, explicit = pdf_eval.question_topics("What are the primary geographies?")
    assert explicit
    assert {"business", "geographic"} <= topics

    topics, explicit = pdf_eval.question_topics("Has the company reported legal battles?")
    assert explicit
    assert "legal" in topics

    topics, explicit = pdf_eval.question_topics(
        "What is FY2019 cash conversion cycle (CCC)?"
    )
    assert explicit
    assert "cash_conversion" in topics

    topics, explicit = pdf_eval.question_topics(
        "If the company liquidated its assets, what could shareholders get?"
    )
    assert explicit
    assert "capital" in topics


def test_business_section_includes_continuation_pages() -> None:
    pages = [
        "table of contents",
        "cover",
        "Item 1. Business Overview. We sell products and services.",
        "Our primary customers are large enterprises.",
        "Cyclicality affects demand.",
        "Item 1A. Risk Factors. Customer concentration is a risk.",
        "Item 7. Management's Discussion and Analysis.",
    ]
    selected = pdf_eval.topic_section_pages(pages, {"business"})
    assert {2, 3, 4} <= selected
    assert 5 not in selected


def test_bm25_keeps_segment_results_page_candidate() -> None:
    pages = [
        "Table of Contents",
        "Segment results managed basis. Net revenue by segment: Consumer 10, Corporate 5.",
        "Consolidated statements of operations. Net revenue 100.",
    ]
    scores = pdf_eval.bm25_page_scores(
        pages,
        ["segment", "results", "net", "revenue"],
        "Which business segment had the lowest net revenue?",
    )
    assert 1 in scores


def test_answer_support_prefers_full_page_evidence() -> None:
    hit = {
        "passage": {
            "filing_id": "SYNTHETIC",
            "locator": "page 4",
            "text": "Revenue was 123 million.",
        }
    }
    page_texts = {("SYNTHETIC", 4): "Revenue was 123 million. Net income was 7 million."}
    assert page_eval.answer_support(hit, "123", page_texts, 2000)
    assert page_eval.answer_support_page(hit, "net income was 7", page_texts)


def test_answer_support_rejects_missing_answer() -> None:
    hit = {
        "passage": {
            "filing_id": "SYNTHETIC",
            "locator": "page 4",
            "text": "Revenue was 123 million.",
        }
    }
    page_texts = {("SYNTHETIC", 4): "Revenue was 123 million."}
    assert not page_eval.answer_support(hit, "456", page_texts, 2000)


def test_title_rerank_promotes_heading_match() -> None:
    hits = [
        {
            "passage": {
                "filing_id": "SYNTHETIC",
                "locator": "page 1",
                "text": "Forward-looking statements and risk factors.",
            }
        },
        {
            "passage": {
                "filing_id": "SYNTHETIC",
                "locator": "page 2",
                "text": "Segment results: net revenue by business segment.",
            }
        },
    ]
    reordered = page_eval.rerank_top1(
        hits,
        "Which business segment had the lowest net revenue?",
        {},
        2000,
        "title",
    )
    assert reordered[0] is hits[1]
    assert page_eval.rerank_top1(
        hits,
        "Which business segment had the lowest net revenue?",
        {},
        2000,
        "off",
    )[0] is hits[0]


def test_full_filing_title_rerank_promotes_heading_match() -> None:
    pages = [
        "Forward-looking statements and risk factors.",
        "Segment results: net revenue by business segment.",
    ]
    ranked = [(0, 0, 10.0), (1, 0, 9.0)]
    reordered = pdf_eval.rerank_title_pages(
        ranked,
        pages,
        "Which business segment had the lowest net revenue?",
        "title",
    )
    assert reordered[0][0] == 1
    assert pdf_eval.rerank_title_pages(
        ranked,
        pages,
        "Which business segment had the lowest net revenue?",
        "off",
    )[0][0] == 0


def test_compact_snippet_match_handles_pdf_spacing() -> None:
    page = "Cash and cash equivalents 1,980 1,101 Short-term investments 2,032"
    evidence = "Cashandcashequivalents 1,980 1,101 Short-terminvestments 2,032"
    assert pdf_eval.snippet_match(page, evidence)
    assert page_eval.compact_match(page, evidence)
    assert not pdf_eval.snippet_match("Revenue 100", "19")


def test_answer_supported_text() -> None:
    assert page_eval.answer_supported_text("Revenue was 123 million.", "123")
    assert page_eval.answer_supported_text("The quick ratio was 0.69.", "0.69")
    assert not page_eval.answer_supported_text("Revenue was 123 million.", "456")


def test_answer_support_requires_every_answer_number() -> None:
    text = "Revenue was 123 million and the margin was 45%."
    assert page_eval.answer_supported_text(text, "123 million and 45%")
    assert not page_eval.answer_supported_text(text, "123 million and 46%")
    assert not page_eval.answer_supported_text("Revenue was 123 million.", "123 and 45")
    # Numeric equality, not a substring of a larger number.
    assert not page_eval.answer_supported_text("Revenue was 1234 million.", "123")
    assert page_eval.answer_supported_text("Revenue was 1,234 million.", "1234")


def test_evaluation_home_requires_managed_marker_or_explicit_force(tmp_path) -> None:
    home = tmp_path / "eval-home"
    home.mkdir()
    sentinel = home / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    with pytest.raises(ValueError, match="unmanaged|Refusing"):
        page_eval.prepare_home(home)
    assert sentinel.exists()

    with pytest.raises(ValueError, match="protected"):
        page_eval.prepare_home(Path.cwd())

    page_eval.prepare_home(home, force=True)
    assert (home / page_eval.MANAGED_HOME_MARKER).exists()
    assert not sentinel.exists()


def test_full_filing_flashrank_rerank_order() -> None:
    class FakeRanker:
        def rerank(self, request):
            return [{"id": 1}, {"id": 0}]

    ranked = [(0, 0, 10.0), (1, 0, 9.0)]
    reordered = pdf_eval.rerank_flashrank_pages(
        ranked, ["first page", "second page"], "query", FakeRanker()
    )
    assert reordered[0][0] == 1


def test_bge_inputs_pads_to_longest_sequence() -> None:
    class Encoding:
        def __init__(self, ids, mask):
            self.ids = ids
            self.attention_mask = mask
            self.type_ids = [0] * len(ids)

    inputs = pdf_eval.bge_inputs(
        [Encoding([1, 2, 3], [1, 1, 1]), Encoding([4], [1])],
        {"input_ids", "attention_mask"},
    )
    assert inputs["input_ids"].tolist() == [[1, 2, 3], [4, 1, 1]]
    assert inputs["attention_mask"].tolist() == [[1, 1, 1], [1, 0, 0]]
