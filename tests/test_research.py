import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from filingcopilot.agent import ResearchAgent
from filingcopilot.api import create_app
from filingcopilot.models import Claim, Filing, ResearchRequest, StatementFact, Submission
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def fact(tools, metric="revenue", year=2025):
    return tools.get_financial_fact("AAPL", metric, year)["evidence"]


def test_as_of_blocks_future_facts_and_passages(tools):
    tools.as_of = date(2025, 10, 30)
    assert not tools.get_financial_fact("AAPL", "revenue", 2025)["found"]
    assert not tools.search_passages("manufacturing supply", "AAPL")["hits"]
    assert tools.list_filings()["filings"] == []


def test_decimal_growth_and_margin(tools):
    current, prior, gross = fact(tools), fact(tools, year=2024), fact(tools, "gross_profit")
    assert tools.calculate("growth", current["id"], prior["id"])["evidence"]["value"] == "100"
    assert tools.calculate("ratio", gross["id"], current["id"])["evidence"]["value"] == "40.0"


def test_no_unobserved_calculation_inputs(tools):
    with pytest.raises(ValueError, match="returned"):
        tools.calculate("growth", "rev25", "rev24")


@pytest.mark.parametrize(
    "field,value",
    [("ticker", "MSFT"), ("unit", "EUR"), ("fiscal_year", 2023), ("metric", "net_income")],
)
def test_incompatible_growth_is_rejected(tools, field, value):
    current, prior = fact(tools), fact(tools, year=2024)
    tools.ledger[prior["id"]] = {**prior, field: value}
    with pytest.raises(ValueError):
        tools.calculate("growth", current["id"], prior["id"])


def test_division_by_zero_rejected(tools):
    current, prior = fact(tools), fact(tools, year=2024)
    tools.ledger[prior["id"]] = {**prior, "value": "0"}
    with pytest.raises(ValueError):
        tools.calculate("growth", current["id"], prior["id"])


def test_margin_cannot_mix_filing_versions(tools):
    revenue, gross = fact(tools), fact(tools, "gross_profit")
    tools.ledger[gross["id"]] = {**gross, "filing_id": "another-version"}
    with pytest.raises(ValueError, match="exact FY"):
        tools.calculate("ratio", gross["id"], revenue["id"])


@pytest.mark.parametrize(
    "claim",
    [
        Claim(kind="fact", evidence_ids=["rev25"], statement=""),
        Claim(kind="fact", evidence_ids=["rev25"], statement="利润为 200"),
        Claim(kind="calculation", evidence_ids=["rev25"], statement=""),
    ],
)
def test_citation_existence_does_not_grant_authority(tools, claim):
    if claim.kind != "fact" or claim.statement:
        fact(tools)
    accepted, errors = verify_submission(Submission(claims=[claim]), tools, "苹果 FY2025")
    assert not accepted and errors


def test_numeric_statement_is_rendered_from_bound_fields(tools):
    fact(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=["rev25"])]), tools, "苹果 FY2025 营收"
    )
    assert not errors
    assert "AAPL FY2025 营业收入" in accepted[0]["text"]


def test_qualitative_narrative_can_bind_a_structured_statement_row(tools):
    filing = Filing.model_validate(tools.corpus.catalog(date(2026, 10, 8), "AAPL")[0])
    row = StatementFact(
        id="customer-row",
        filing_id=filing.id,
        ticker="AAPL",
        statement="other",
        metric="customer_revenue_concentration",
        label="Major customer revenue",
        concept="custom:MajorCustomerRevenue",
        value="10",
        unit="percent",
        fiscal_year=2025,
        end=filing.period_end,
        source_url=filing.source_url,
        locator="customer concentration row",
        dimensions={"customer": "major"},
        dimension_labels={"customer": "Major customer"},
    )
    tools.corpus.add_statement_facts(filing, [row])
    evidence = tools.get_statement(
        "AAPL", filing_id=filing.id, query="customer concentration", limit=5
    )["evidence"][0]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="narrative",
                    statement="年报披露了主要客户集中度风险。",
                    evidence_ids=[evidence["id"]],
                )
            ]
        ),
        tools,
        "苹果 FY2025 客户集中度风险",
        semantic_review=lambda _question, claims: {
            "verdicts": [
                {"index": item["index"], "supported": True, "reason": "structured row"}
                for item in claims
            ]
        },
        focus_ticker="AAPL",
    )
    assert not errors
    assert accepted[0]["evidence_ids"] == [evidence["id"]]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="narrative",
                    statement="主要客户占收入10%。",
                    evidence_ids=[evidence["id"]],
                )
            ]
        ),
        tools,
        "苹果 FY2025 客户集中度风险",
        semantic_review=lambda _question, claims: {
            "verdicts": [
                {"index": item["index"], "supported": True, "reason": "not reached"}
                for item in claims
            ]
        },
        focus_ticker="AAPL",
    )
    assert not accepted and any("typed fact" in error for error in errors)


def test_requested_metric_is_enforced(tools):
    item = fact(tools, "gross_profit")
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[item["id"]])]),
        tools,
        "苹果 FY2025 营收",
    )
    assert not accepted and any("metric" in error for error in errors)


@pytest.mark.parametrize(
    "question",
    [
        "苹果 FY2024、FY2025 营收",
        "苹果和微软 FY2025 营收",
    ],
)
def test_one_correct_number_does_not_complete_a_comparison(tools, question):
    item = fact(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=[item["id"]])]),
        tools,
        question,
    )
    assert accepted and any("missing" in error for error in errors)


def test_general_direction_contradiction_is_rejected(tools):
    tools.search_passages("supply", "AAPL")
    tools.ledger["p1"]["text"] = "Revenue decreased because of lower demand."
    submission = Submission(
        claims=[
            Claim(
                kind="narrative",
                evidence_ids=["p1"],
                statement="营业收入上升。",
            )
        ]
    )

    def reviewer(q, n):
        return {"verdicts": [{"index": 0, "supported": True, "reason": "ok"}]}

    accepted, errors = verify_submission(submission, tools, "苹果营收变化", reviewer)
    assert not accepted and any("direction" in error for error in errors)


def test_company_and_year_mismatch_rejected(tools):
    fact(tools)
    for question in ("微软 FY2025 营收", "苹果 FY2024 营收"):
        accepted, errors = verify_submission(
            Submission(claims=[Claim(kind="fact", evidence_ids=["rev25"])]), tools, question
        )
        assert not accepted and errors


def test_real_citation_with_negated_claim_is_rejected(tools):
    tools.search_passages("default", "AAPL")
    claim = Claim(kind="narrative", statement="苹果已经违约。", evidence_ids=["p1"])

    def reviewer(q, n):
        return {
            "verdicts": [
                {
                    "index": 0,
                    "supported": False,
                    "reason": "source explicitly says no default has occurred",
                }
            ]
        }

    accepted, errors = verify_submission(Submission(claims=[claim]), tools, "苹果的风险", reviewer)
    assert not accepted and "no default" in errors[0]


def test_semantic_outage_fails_closed(tools):
    tools.search_passages("supply", "AAPL")
    claim = Claim(kind="narrative", statement="存在供应风险。", evidence_ids=["p1"])

    def reviewer(q, n):
        raise ConnectionError("outage")

    accepted, errors = verify_submission(Submission(claims=[claim]), tools, "风险", reviewer)
    assert not accepted and errors


def test_malformed_reviewer_boolean_is_not_coerced_into_approval(tools):
    tools.search_passages("supply", "AAPL")
    claim = Claim(kind="narrative", evidence_ids=["p1"], statement="存在供应风险。")

    def reviewer(q, n):
        return {"verdicts": [{"index": 0, "supported": "yes", "reason": "ok"}]}

    accepted, errors = verify_submission(Submission(claims=[claim]), tools, "苹果风险", reviewer)
    assert not accepted and errors


def test_missing_growth_calculation_is_not_complete(tools):
    fact(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="fact", evidence_ids=["rev25"])]), tools, "FY2025 营收同比"
    )
    assert accepted and any("calculate" in e for e in errors)


def test_narrative_cannot_bypass_numeric_rendering(tools):
    tools.search_passages("supply", "AAPL")
    submission = Submission(
        claims=[
            Claim(
                kind="narrative",
                evidence_ids=["p1"],
                statement="供应收入增长 99%，产生 100 亿美元收入。",
            )
        ]
    )
    accepted, errors = verify_submission(submission, tools, "供应链表现")
    assert not accepted and any("typed" in e for e in errors)


def test_gross_profit_increase_cannot_be_translated_as_margin_increase(tools):
    tools.search_passages("supply", "AAPL")
    tools.ledger["p1"]["text"] = (
        "Gross margin increased $8.2 billion driven by Azure. "
        "Gross margin percentage decreased due to AI infrastructure."
    )
    submission = Submission(
        claims=[
            Claim(kind="narrative", evidence_ids=["p1"], statement="Azure 增长推动毛利率提升。")
        ]
    )
    accepted, errors = verify_submission(submission, tools, "毛利率原因")
    assert not accepted and any("gross profit" in e for e in errors)


def test_retrieval_keeps_company_date_and_fiscal_scope(tools):
    assert tools.search_passages("supply", "AAPL", fiscal_year=2025)["hits"]
    assert not tools.search_passages("supply", "MSFT")["hits"]
    assert not tools.search_passages("supply", "AAPL", fiscal_year=2024)["hits"]


def test_broken_optional_vector_index_preserves_lexical_results(tools, monkeypatch):
    tools.retriever.settings = Settings(
        home=tools.corpus.home, embedding_mode="local", rerank_mode="anchor"
    )

    def broken(passages):
        raise ValueError("corrupted vector file")

    monkeypatch.setattr(tools.retriever, "_vectors", broken)
    result = tools.search_passages("supply", "AAPL")
    assert result["hits"] and result["backend"] == "fts5"
    assert result["embedding_unavailable"] == "ValueError"


class ScriptedGateway:
    def __init__(self, turns):
        self.turns = iter(turns)

    def complete(self, messages, tools, review=False):
        turn = next(self.turns)
        calls = [
            {
                "id": "call-" + str(i),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
            for i, (name, arguments) in enumerate(turn)
        ]
        return {
            "message": {"role": "assistant", "content": None, "tool_calls": calls},
            "usage": {"total_tokens": 10},
            "model": "scripted-test",
        }


def test_real_langgraph_loop_observes_tools_and_submits(tools):
    gateway = ScriptedGateway(
        [
            [("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})],
            [
                (
                    "submit_answer",
                    {
                        "claims": [{"kind": "fact", "evidence_ids": ["rev25"], "statement": ""}],
                        "gaps": [],
                    },
                )
            ],
        ]
    )
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=4), gateway)
    result = agent.run(ResearchRequest(question="苹果 FY2025 营收"), tools)
    assert result.status == "answered" and result.usage["model_calls"] == 2
    assert result.answer[0]["evidence_ids"] == ["rev25"]


def test_submission_stops_later_tools_in_the_same_batch(tools):
    gateway = ScriptedGateway(
        [
            [("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})],
            [
                ("submit_answer", {"claims": [{"kind": "fact", "evidence_ids": ["rev25"]}]}),
                (
                    "get_financial_fact",
                    {"ticker": "AAPL", "metric": "gross_profit", "fiscal_year": 2025},
                ),
            ],
        ]
    )
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=4), gateway).run(
        ResearchRequest(question="苹果 FY2025 营收"),
        tools,
    )
    assert result.status == "answered"
    assert "gross25" not in tools.ledger
    assert (
        len(
            [
                step
                for step in result.trace
                if step["stage"] == "tool" and step["tool"] == "get_financial_fact"
            ]
        )
        == 1
    )


def test_repair_receives_responses_for_skipped_tool_call_ids(tools):
    class ProtocolGateway(ScriptedGateway):
        def complete(self, messages, schemas, review=False):
            pending = set()
            for message in messages:
                if message["role"] == "assistant":
                    assert not pending
                    pending = {call["id"] for call in message.get("tool_calls", [])}
                elif message["role"] == "tool":
                    pending.remove(message["tool_call_id"])
            assert not pending
            return super().complete(messages, schemas, review)

    gateway = ProtocolGateway(
        [
            [("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})],
            [
                ("submit_answer", {"claims": [{"kind": "fact", "evidence_ids": ["invented"]}]}),
                (
                    "get_financial_fact",
                    {"ticker": "AAPL", "metric": "gross_profit", "fiscal_year": 2025},
                ),
            ],
            [("submit_answer", {"claims": [{"kind": "fact", "evidence_ids": ["rev25"]}]})],
        ]
    )
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=5), gateway).run(
        ResearchRequest(question="苹果 FY2025 营收"),
        tools,
    )
    assert result.status == "answered" and "gross25" not in tools.ledger
    assert any(step["stage"] == "tool_skipped" for step in result.trace)


def test_round_budget_actually_stops_the_graph(tools):
    gateway = ScriptedGateway([[("list_filings", {})]] * 10)
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), gateway)
    result = agent.run(ResearchRequest(question="苹果表现"), tools)
    assert result.status == "abstained" and result.usage["model_calls"] == 2


def test_agent_repairs_a_fabricated_citation(tools):
    gateway = ScriptedGateway(
        [
            [("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})],
            [
                (
                    "submit_answer",
                    {
                        "claims": [{"kind": "fact", "evidence_ids": ["invented"], "statement": ""}],
                        "gaps": [],
                    },
                )
            ],
            [
                (
                    "submit_answer",
                    {
                        "claims": [{"kind": "fact", "evidence_ids": ["rev25"], "statement": ""}],
                        "gaps": [],
                    },
                )
            ],
        ]
    )
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=5), gateway).run(
        ResearchRequest(question="苹果 FY2025 营收"), tools
    )
    assert result.status == "answered"
    assert len([t for t in result.trace if t["stage"] == "verify"]) == 2


def test_agent_reserves_rounds_for_submission(tools):
    class BudgetGateway(ScriptedGateway):
        offered = []

        def complete(self, messages, schemas, review=False):
            self.offered.append([s["function"]["name"] for s in schemas])
            return super().complete(messages, schemas, review)

    gateway = BudgetGateway(
        [
            [("get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})],
            [
                (
                    "submit_answer",
                    {
                        "claims": [{"kind": "fact", "evidence_ids": ["rev25"], "statement": ""}],
                        "gaps": [],
                    },
                )
            ],
        ]
    )
    result = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=3), gateway).run(
        ResearchRequest(question="苹果 FY2025 营收"), tools
    )
    assert result.status == "answered" and gateway.offered[-1] == ["submit_answer"]


def test_api_reference_run_and_secret_free_health(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    client = TestClient(create_app(service))
    assert client.get("/").status_code == 200
    assert client.get("/api/connection").json()["mcpServers"]["filingcopilot"]["cwd"]
    old_catalog = client.get("/api/catalog?as_of=2020-01-01").json()
    assert old_catalog["fact_count"] == old_catalog["passage_count"] == 0
    assert "api_key" not in client.get("/api/health").json()
    answer = client.post(
        "/api/research",
        json={
            "question": "苹果 FY2025 营收",
            "ticker": "AAPL",
            "mode": "reference",
            "as_of": "2026-10-08",
        },
    )
    assert answer.status_code == 200
    assert answer.json()["usage"]["model_calls"] == 0
    assert client.get("/api/runs").json()[0]["id"] == answer.json()["id"]
    assert (
        client.post("/api/research", json={"question": "苹果营收为什么增长？", "mode": "agent"}).status_code
        == 422
    )
