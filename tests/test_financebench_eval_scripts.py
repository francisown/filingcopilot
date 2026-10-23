"""Regression tests for the FinanceBench evaluation scripts.

These tests intentionally use synthetic pages: no PDF downloads, no model, no
network. They pin the routing decisions that the full-filing evaluator relies on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
