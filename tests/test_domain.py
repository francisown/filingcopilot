import json
from datetime import date
from decimal import Decimal

import pytest

from filingcopilot.domain import DEFAULT_DOMAIN, load_domain
from filingcopilot.models import Claim, ResearchRequest, Submission
from filingcopilot.retrieval import expand_query, rewrite_query
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.tools import format_amount, tool_schemas
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_configuration_drives_every_entry_point(tmp_path):
    path = tmp_path / "domain.json"
    path.write_text(
        json.dumps(
            {
                "companies": {
                    "TEST": {"cik": "0000000001", "name": "Test Company", "aliases": ["测试公司"]}
                },
                "metrics": {
                    "research_expense": {
                        "label": "研发支出",
                        "concepts": ["ResearchAndDevelopmentExpense"],
                        "aliases": ["research expense", "研发"],
                    }
                },
                "query_expansions": {"研发": "research development expense"},
                "currencies": {"CAD": "加元"},
            }
        ),
        encoding="utf-8",
    )
    service = FilingCopilot(
        Settings(home=tmp_path / "data", domain_config=path, embedding_mode="off")
    )
    domain = service.domain
    assert domain.company_scope("测试公司研发") == {"TEST"}
    assert domain.requests("测试公司研发")[0] == {"research_expense"}
    schemas = tool_schemas(domain)
    assert (
        "research_expense" in schemas[1]["function"]["parameters"]["properties"]["metric"]["enum"]
    )
    assert "research_expense" in service.tools(date.today()).list_filings()["supported_metrics"]
    assert "development" in expand_query("研发", domain.query_expansions)
    assert "加元" in format_amount(Decimal("250000000"), "CAD", domain)
    assert "加元" not in format_amount(Decimal("250000000"), "CAD")


def test_invalid_configuration_fails_clearly(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"companies": {"TEST": {"cik": "../../secret", "name": "bad"}}}))
    with pytest.raises(ValueError):
        load_domain(path)


@pytest.mark.parametrize(
    "question,metrics,ratios",
    [
        ("cost of revenue", {"cost_of_revenue"}, set()),
        ("营业利润率", set(), {"operating_margin"}),
        ("营收同比和毛利率", {"revenue"}, {"gross_margin"}),
        ("cash flow and cash balance", {"operating_cash_flow", "cash"}, set()),
    ],
)
def test_overlapping_aliases_keep_metric_identity(question, metrics, ratios):
    assert DEFAULT_DOMAIN.requests(question) == (metrics, ratios)


def test_ticker_requires_word_boundary():
    assert not DEFAULT_DOMAIN.company_scope("metadata and aaplus")
    assert DEFAULT_DOMAIN.company_scope("AAPL FY2025") == {"AAPL"}


def test_narrative_tool_contract_excludes_unrelated_numeric_overviews():
    names = {
        schema["function"]["name"]
        for schema in tool_schemas(DEFAULT_DOMAIN, narrative="risk")
    }
    assert "search_passages" in names
    assert "get_statement" in names
    assert "submit_answer" in names
    assert "get_company_overview" not in names
    assert "get_stock_market" not in names


def test_narrative_claim_contract_is_bounded_before_review():
    submit = next(
        schema
        for schema in tool_schemas(DEFAULT_DOMAIN, claim_category="risk")
        if schema["function"]["name"] == "submit_answer"
    )
    claims = submit["function"]["parameters"]["properties"]["claims"]
    assert claims["maxItems"] == 5
    assert claims["items"]["properties"]["statement"]["maxLength"] == 700
    assert "one independently supportable proposition" in submit["function"]["description"]


def test_query_rewrite_normalizes_punctuation_and_domain_aliases(tmp_path):
    path = tmp_path / "domain.json"
    path.write_text(
        json.dumps(
            {
                "metrics": {
                    "custom_metric": {
                        "label": "客户留存率",
                        "concepts": ["company:CustomerRetention"],
                        "aliases": ["retention", "留存"],
                        "unit": "%",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    domain = load_domain(path)
    result = rewrite_query("客户留存率，FY2025", domain=domain)

    assert result["normalized"] == "客户留存率 FY2025"
    assert "custom metric" in result["expanded"]
    assert "retention" in result["expanded"]
    assert result["added_terms"]


def test_query_rewrite_matches_english_aliases_without_case_sensitivity():
    result = rewrite_query("Revenue VALUATION", aliases={"valuation": "value multiple"})

    assert "value multiple" in result["expanded"]


def test_query_rewrite_canonicalizes_finance_abbreviations_for_bilingual_search():
    result = rewrite_query("AAPL P/E、YoY FCF、CapEx 和 TTM")

    assert "price earnings ratio" in result["normalized"]
    assert "year over year" in result["normalized"]
    assert "free cash flow" in result["normalized"]
    assert "capital expenditures" in result["normalized"]
    assert "trailing twelve months" in result["normalized"]
    assert "、" not in result["normalized"]
    assert result["canonical_terms"] == ["P/E", "YoY", "FCF", "CapEx", "TTM"]
    # Word boundaries must prevent PE from rewriting the middle of a company name.
    assert "price earnings ratio" not in rewrite_query("Apple revenue")['normalized']


def test_retrieval_result_keeps_canonical_terms_for_audit(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path / "data", embedding_mode="off"))

    result = service.retriever.search(
        "苹果 P/E 和 YoY FCF",
        date(2026, 10, 8),
        ticker="AAPL",
        lexical_only=True,
    )

    assert result["query_rewrite"]["canonical_terms"] == ["P/E", "YoY", "FCF"]


def test_excerpt_is_verified_against_original_and_cannot_bypass_agent_review(tools):
    tools.search_passages("supply", "AAPL")
    claim = Claim(kind="excerpt", evidence_ids=["p1"], statement=tools.ledger["p1"]["text"])
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果风险", allow_excerpts=True
    )
    assert accepted and not errors
    assert verify_submission(Submission(claims=[claim]), tools, "苹果风险")[1]
    claim.statement += " A fabricated conclusion."
    assert not verify_submission(
        Submission(claims=[claim]), tools, "苹果风险", allow_excerpts=True
    )[0]


def test_reference_runs_shared_verifier_and_handles_two_calculations(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    result = service.research(
        ResearchRequest(question="苹果 FY2025 营收同比和毛利率", mode="reference")
    )
    assert result.status == "answered"
    assert result.trace[-1]["stage"] == "verify" and not result.trace[-1]["findings"]
    calculations = [e for e in result.evidence if e["kind"] == "calculation"]
    assert {e["operation"] for e in calculations} == {"growth", "ratio"}
    excerpt = service.research(ResearchRequest(question="苹果 FY2025 供应链风险", mode="reference"))
    assert excerpt.status == "partial" and excerpt.answer
    assert excerpt.trace[-1]["stage"] == "verify"
    assert all(c["verification"] == "exact_source_excerpt" for c in excerpt.answer)


def test_small_currency_amount_does_not_round_to_zero():
    assert "0.00 亿" not in format_amount(Decimal("12.50"), "EUR")
    assert "12.50 欧元" in format_amount(Decimal("12.50"), "EUR")


def test_real_net_income_cannot_answer_a_revenue_question(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    tools = service.tools(date(2026, 10, 8))
    evidence = tools.get_financial_fact("AAPL", "net_income", 2025)["evidence"]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[evidence["id"]])]),
        tools,
        "苹果 FY2025 营收是多少？",
    )
    assert not accepted and any("metric" in error for error in errors)
