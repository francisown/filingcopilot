"""Company-research module contracts: registry, routing, page payload and limits.

The nine module summaries must each carry their own question, points,
prohibitions, boundary sentence and gap text; every module question must stay on
the generic evidence path so the section brief controls the format, and the
agent must reject over-long or over-count submissions instead of trimming them.
"""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from filingcopilot.agent import ResearchAgent
from filingcopilot.company_modules import (
    MODULES,
    module_contract,
    module_instruction,
    statement_char_limit,
)
from filingcopilot.company_page import PAGE_SECTIONS, build_company_page
from filingcopilot.market import MarketData
from filingcopilot.models import Filing, Passage, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.status import classify_status_reason


def service_at(tmp_path):
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))


def ingest_annual(service, ticker="AMZN"):
    filing = Filing(
        id=f"{ticker}:annual",
        ticker=ticker,
        company=ticker,
        cik="0001018724",
        accession="0001018724-26-000001",
        form="10-K",
        filed="2026-02-01",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/annual.htm",
        content_sha256="a" * 64,
    )
    service.corpus.ingest(
        filing,
        [],
        [
            Passage(
                id=f"{ticker}-business",
                filing_id=filing.id,
                ticker=ticker,
                section="Item 1. Business",
                text="We sell products and services to customers worldwide.",
                source_url=filing.source_url,
                locator="paragraph 1",
            )
        ],
    )
    return filing


def market_stub(tmp_path):
    """A synthetic Yahoo chart keeps module preparation off the live provider."""
    stamp = int(datetime(2026, 10, 7, 16, 0, tzinfo=ZoneInfo("America/New_York")).timestamp())
    body = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": "AMZN",
                        "currency": "USD",
                        "instrumentType": "EQUITY",
                        "longName": "Amazon.com, Inc.",
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": 100,
                        "regularMarketTime": stamp,
                    },
                    "timestamp": [stamp],
                    "indicators": {"quote": [{"close": [100]}], "adjclose": [{"adjclose": [100]}]},
                }
            ],
        }
    }

    def handler(request):
        if "chart" in str(request.url):
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return MarketData(tmp_path / "market-data", client=httpx.Client(transport=httpx.MockTransport(handler)))


def offline_prepare(ticker, as_of):
    """The preparation contract here must not trigger live source acquisition."""
    return {
        "found": False,
        "checked": False,
        "ticker": ticker,
        "acquired": [],
        "warning": "测试环境不获取远程年报；本测试只验证准备阶段的工具调用。",
    }


def test_module_registry_covers_every_page_section_exactly_once():
    assert sorted(MODULES) == sorted(PAGE_SECTIONS)
    assert len({module.boundary for module in MODULES.values()}) == len(MODULES)
    assert len({module.empty_gap for module in MODULES.values()}) == len(MODULES)
    for key, module in MODULES.items():
        assert module.key == key
        assert module_contract(key) is module
        assert module_contract("unknown") is None
        assert module.title and module.title_en and module.focus
        assert module.points and module.prohibitions
        assert module.kind in ("analysis", "factual")
        assert module.question.endswith("?")


def test_module_instruction_carries_scope_prohibitions_and_language_limit():
    analysis = module_instruction("business", "zh")
    assert "Business model" in analysis
    assert "220 characters" in analysis
    assert "no amounts" in analysis
    assert "do not fill in business lines" in analysis
    assert "one to three kind=analysis claims" in analysis
    assert "how the company earns revenue and who pays" in analysis
    assert "not stated in the cited excerpts" in analysis
    factual = module_instruction("audit", "en")
    assert "factual restatement" in factual
    assert "300 characters" in factual
    assert "no financial-health judgement" in factual
    assert "one to three kind=narrative claims" in factual
    assert "not stated in the cited excerpts" in factual
    assert statement_char_limit("zh") == 220
    assert statement_char_limit("en") == 300
    valuation = module_instruction("valuation", "zh")
    assert "state each observed annual-basis or supplier-TTM" in valuation
    assert "target price" in valuation


def test_every_module_question_stays_on_the_generic_evidence_path(tmp_path):
    """No module question may fall into market/valuation/quarterly/update branches."""
    service = service_at(tmp_path)
    for ticker in ("AMZN", "TSM"):
        for key, module in MODULES.items():
            plan = service.research_plan(
                ResearchRequest(
                    question=module.question,
                    ticker=ticker,
                    as_of=date(2026, 10, 8),
                    mode="agent",
                    module=key,
                )
            )
            assert plan["ready"], (ticker, key, plan.get("message"))
            assert plan["kind"] == "filing", (ticker, key, plan["kind"])
            assert plan["uses_model"] is True, (ticker, key)


def test_page_sections_carry_their_module_contract(tmp_path):
    service = service_at(tmp_path)
    ingest_annual(service)
    page = build_company_page(service, "AMZN", date(2026, 10, 8), include_market=False)
    assert len(page["sections"]) == len(MODULES)
    assert page["source_coverage"]["annual_forms"] == ["10-K"]
    assert "未登记的 6-K" in page["source_coverage"]["limitations"][1]
    for section in page["sections"]:
        module = MODULES[section["key"]]
        contract = section["module"]
        assert contract == {
            "kind": module.kind,
            "question": module.question,
            "boundary": module.boundary,
            "empty_gap": module.empty_gap,
        }


def module_state(claims, module="business", language="zh"):
    arguments = {"claims": claims, "gaps": []}
    question = MODULES[module].question if module else "Summarize the company using only disclosed evidence."
    return {
        "messages": [
            {
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "submit_answer",
                            "arguments": json.dumps(arguments),
                        },
                    }
                ]
            }
        ],
        "question": question,
        "module": module,
        "output_language": language,
        "tool_count": 0,
        "usage": {},
        "seen": set(),
        "trace": [],
    }


def claim(statement, kind="analysis"):
    return {"kind": kind, "statement": statement, "evidence_ids": ["e1"]}


def test_module_submission_limits_are_enforced_without_trimming(tmp_path):
    agent = ResearchAgent(Settings(home=tmp_path, embedding_mode="off"), None)
    accepted = agent.execute(module_state([claim("公司通过两条业务线获取收入。"), claim("客户结构以企业客户为主。")]))
    assert accepted["action"] == "verify"
    assert len(accepted["submission"].claims) == 2

    too_many = agent.execute(
        module_state([claim("结论一。" * 20), claim("结论二。" * 20), claim("结论三。" * 20), claim("结论四。" * 20)])
    )
    assert too_many["action"] == "reason"
    assert too_many["submission"] is None
    assert "one to three" in too_many["trace"][-1]["result"]["error"]

    too_long = agent.execute(module_state([claim("长" * 221)]))
    assert too_long["action"] == "reason"
    assert "220" in too_long["trace"][-1]["result"]["error"]

    at_limit = agent.execute(module_state([claim("长" * 220)]))
    assert at_limit["action"] == "verify"

    english_limit = agent.execute(module_state([claim("word " * 50)], language="en"))
    assert english_limit["action"] == "verify"

    no_module = agent.execute(module_state([claim("结论。"), claim("结论。"), claim("结论。"), claim("结论。")], module=None))
    assert no_module["action"] == "verify", "absent module keeps the previous request behaviour"

    factual = agent.execute(module_state([claim("原文整理。", kind="analysis")], module="ownership"))
    assert factual["action"] == "reason"
    assert "kind=narrative" in factual["trace"][-1]["result"]["error"]


def test_module_requests_validate_and_default_metadata(tmp_path):
    # A legacy request without a module still validates unchanged.
    assert ResearchRequest(question="What are the main risks?", ticker="AMZN").module is None
    # Unknown module keys are rejected at the request boundary.
    with pytest.raises(ValidationError):
        ResearchRequest(question="What are the main risks?", ticker="AMZN", module="unknown")
    assert MODULES["business"].title == "商业模式"
    assert "kind=narrative" in module_instruction("recent_events")


def test_factual_modules_use_a_shorter_model_round_budget():
    """Reference-style sections should not spend the full analysis budget."""
    from filingcopilot.agent import module_round_limit

    assert module_round_limit("ownership", 10) == 4
    assert module_round_limit("audit", 10) == 4
    assert module_round_limit("recent_events", 10) == 4
    assert module_round_limit("business", 10) == 10
    assert module_round_limit("valuation", 3) == 3


def test_risk_questions_use_the_six_round_budget_only():
    from filingcopilot.agent import is_risk_question, question_round_limit

    assert is_risk_question("Which risks should an analyst track?")
    assert is_risk_question("英伟达的风险有哪些？")
    assert not is_risk_question("What was the revenue growth?")
    assert not is_risk_question("asterisk marker")
    assert question_round_limit(None, "Which risks matter?", 8, 6) == 6
    assert question_round_limit(None, "What was revenue?", 8, 6) == 8
    assert question_round_limit("risk", "What are the main risks?", 8, 6) == 6
    assert question_round_limit("ownership", "Who owns shares?", 8, 6) == 4


def test_status_reason_distinguishes_budget_and_evidence_gaps():
    assert classify_status_reason(
        {
            "status": "abstained",
            "answer": [],
            "gaps": ["本轮研究时间预算已用完。"],
            "trace": [{"stage": "deadline"}],
            "usage": {"budget_status": "expired"},
        }
    ) == "budget_expired"
    assert classify_status_reason(
        {"status": "abstained", "answer": [], "gaps": ["当前证据不足"], "trace": [], "usage": {}}
    ) == "no_supported_evidence"
    assert classify_status_reason(
        {"status": "partial", "answer": [{"kind": "analysis"}], "gaps": ["资料尚有缺口"], "trace": [], "usage": {}}
    ) == "evidence_gap"


def test_numeric_modules_prepare_typed_context_before_model_draft(tmp_path, monkeypatch):
    """A section brief must enter the model with its relevant numbers prepared."""

    class CaptureGateway:
        def complete(self, messages, schemas, review=False):
            raise RuntimeError("Controlled boundary capture; no LLM request")

    service = service_at(tmp_path)
    ingest_annual(service)
    # The pre-draft preparation must be provable without a live market or SEC
    # acquisition; serve a synthetic chart and keep source refresh offline.
    service.market = market_stub(tmp_path)
    service.discovery.prepare_valuation = offline_prepare
    for key, required in {
        "operating_change": {
            "get_company_overview",
            "get_quarterly_overview",
            "get_company_updates",
        },
        "cashflow": {"get_company_overview", "get_cashflow"},
        "valuation": {"get_company_overview", "get_stock_valuation"},
    }.items():
        tools = service.tools(date(2026, 10, 8))
        calls = []
        for name in required:
            original = getattr(tools, name)

            def wrapped(*args, _name=name, _original=original, **kwargs):
                calls.append(_name)
                return _original(*args, **kwargs)

            monkeypatch.setattr(tools, name, wrapped)
        answer = ResearchAgent(Settings(home=tmp_path, embedding_mode="off"), CaptureGateway()).run(
            ResearchRequest(
                question=MODULES[key].question,
                ticker="AMZN",
                as_of=date(2026, 10, 8),
                mode="agent",
                module=key,
            ),
            tools,
        )
        assert required <= set(calls), (key, required, calls, answer.trace)


def test_agent_mode_module_request_without_model_fails_honestly(tmp_path):
    service = service_at(tmp_path)
    request = ResearchRequest(
        question=MODULES["risk"].question,
        ticker="AMZN",
        as_of=date(2026, 10, 8),
        mode="agent",
        module="risk",
    )
    with pytest.raises(ValueError, match="模型"):
        service._research(request)


def test_factual_module_keeps_observed_source_excerpt_after_model_failure(tmp_path):
    """A factual section should retain exact observed text when drafting fails."""
    service = service_at(tmp_path)
    ingest_annual(service)
    audit_filing = Filing(
        id="AMZN:audit",
        ticker="AMZN",
        company="AMZN",
        cik="0001018724",
        accession="0001018724-26-000004",
        form="10-K",
        filed="2026-02-06",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/audit.htm",
        content_sha256="b" * 64,
    )
    service.corpus.ingest(
        audit_filing,
        [],
        [
            Passage(
                id="AMZN-audit",
                filing_id=audit_filing.id,
                ticker="AMZN",
                section="Item 8. Financial Statements and Supplementary Data",
                text="We have audited the consolidated financial statements. In our opinion, they present fairly in all material respects.",
                source_url=audit_filing.source_url,
                locator="audit paragraph",
            )
        ],
    )
    tools = service.tools(date(2026, 10, 8))
    source = service.corpus.evidence("AMZN-audit", date(2026, 10, 8))
    tools.ledger[source["id"]] = source
    state = {
        "module": "audit",
        "question": MODULES["audit"].question,
        "focus_ticker": "AMZN",
        "tools": tools,
        "trace": [
            {
                "stage": "tool",
                "tool": "search_passages",
                "result": {"hits": [{"passage": source}]},
            },
            {
                "stage": "tool",
                "tool": "submit_answer",
                "result": {"error": "Submission has an unknown field", "recoverable": True},
            },
        ],
        "verified": [],
        "errors": ["Model request failed; no answer was fabricated"],
        "submission": None,
        "retained_gaps": [],
        "numeric_coverage_errors": [],
        "tool_count": 0,
        "usage": {},
    }
    agent = ResearchAgent(Settings(home=tmp_path, embedding_mode="off"), None)

    gaps, _ = agent._complete_filing_answer(state, [])

    assert state["verified"] == [
        {
            "index": 0,
            "kind": "excerpt",
            "text": source["text"],
            "evidence_ids": [source["id"]],
            "verification": "exact_source_excerpt",
        }
    ]
    assert any("逐字原文材料" in gap for gap in gaps)


def test_module_statements_accept_both_claim_shapes():
    """The module trace must count delivered (text) and draft (statement) claims."""
    from filingcopilot.agent import module_statements

    delivered = [
        {"kind": "analysis", "text": "已交付分析。"},
        {"kind": "narrative", "text": "叙述。"},
        {"kind": "fact", "text": "数字。"},
    ]
    assert len(module_statements(delivered)) == 2
    drafts = [{"kind": "analysis", "statement": "草稿。"}, {"kind": "excerpt", "statement": "摘录。"}]
    assert len(module_statements(drafts)) == 1
    assert module_statements([{"kind": "analysis", "text": ""}]) == []


@pytest.mark.slow
def test_module_brief_reaches_the_model_boundary_and_records_its_trace(tools, tmp_path):
    """The module brief must be appended to the system prompt of the real run loop."""

    class CaptureGateway:
        def complete(self, messages, schemas, review=False):
            self.messages = messages
            raise RuntimeError("Controlled boundary capture; no LLM request")

    gateway = CaptureGateway()
    answer = ResearchAgent(Settings(home=tmp_path), gateway).run(
        ResearchRequest(
            language="zh",
            question=MODULES["business"].question,
            ticker="AAPL",
            as_of="2026-10-08",
            module="business",
        ),
        tools,
    )
    system = gateway.messages[0]["content"]
    assert "COMPANY-RESEARCH MODULE BRIEF — Business model" in system
    assert "do not fill in business lines" in system
    assert "one to three kind=analysis claims" in system
    module_trace = [t for t in answer.trace if t["stage"] == "module_summary"]
    assert module_trace, "the delivered answer must record its module summary"
    assert module_trace[-1]["module"] == "business"
    assert module_trace[-1]["kind"] == "analysis"
    assert module_trace[-1]["boundary"] == MODULES["business"].boundary
    assert module_trace[-1]["status"] == "incomplete"
