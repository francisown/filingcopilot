"""Synthetic tests for FinDER company-scoped reference retrieval."""

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


finder_eval = load_script("finder_eval", "evaluate_finder.py")


def test_company_header_ignores_prose() -> None:
    assert (
        finder_eval.company_from_references(
            [
                "Employee Population. We employed people at the Company.",
                "ACME CORP\nRevenue was 10 million.",
            ]
        )
        == "ACME CORP"
    )
    assert (
        finder_eval.company_from_references(
            ["We believe our people make the Company successful."]
        )
        == ""
    )


def test_company_scope_falls_back_to_category() -> None:
    rows = [
        {
            "_id": 1,
            "text": "What was ACME revenue?",
            "category": "metrics",
            "references": ["ACME CORP\nRevenue was 10 million."],
            "answer": "10 million",
            "reasoning": True,
        },
        {
            "_id": 2,
            "text": "What was revenue?",
            "category": "metrics",
            "references": ["OTHER COMPANY\nRevenue was 20 million."],
            "answer": "20 million",
            "reasoning": True,
        },
    ]
    _, row_scope, company_scopes = finder_eval.build_company_scope(rows)
    assert company_scopes >= 1
    assert row_scope[1] == "company:ACME CORP"
    assert row_scope[2] == "category:metrics"


def test_generic_single_word_alias_does_not_scope() -> None:
    rows = [
        {
            "_id": 3,
            "text": "What is global revenue?",
            "category": "metrics",
            "references": ["GLOBAL PAYMENTS INC\nRevenue was 30 million."],
            "answer": "30 million",
            "reasoning": True,
        }
    ]
    _, row_scope, _ = finder_eval.build_company_scope(rows)
    assert row_scope[3] == "category:metrics"


def test_answer_support_requires_every_answer_number() -> None:
    text = "Revenue was 30 million and the margin was 12%."
    assert finder_eval.answer_supported_text(text, "30 million and 12%")
    assert not finder_eval.answer_supported_text(text, "30 million and 13%")
    assert not finder_eval.answer_supported_text("Revenue was 30 million.", "30 and 12")
    assert not finder_eval.answer_supported_text("Revenue was 300 million.", "30")
    assert finder_eval.answer_supported_text("Revenue was 1,234 million.", "1234")


def test_answer_support_reports_partial_and_calculation() -> None:
    partial = finder_eval.answer_support_report(
        ["Revenue was 30 million."], "Revenue was 30 million and 12% margin."
    )
    assert partial["status"] == "partial"
    calc = finder_eval.answer_support_report(
        ["Revenue was 539.2 million and 427.7 million."],
        "The increase was 111.5 million, calculated as 539.2 minus 427.7.",
    )
    assert calc["status"] == "full"
    assert calc["calculation_supported"]
