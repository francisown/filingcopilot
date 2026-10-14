import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from test_cashflow import cash_fixture
from test_ttm import rolling_fixture

from filingcopilot.audit import audit_package
from filingcopilot.models import Claim, Fact, ResearchRequest, Submission
from filingcopilot.periods import period_label
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def net_fixture(tmp_path):
    service, filing = cash_fixture(tmp_path, ticker="AMZN", form="10-Q")
    purchases = [
        f
        for year in (2026, 2025)
        for f in service.corpus.facts(
            "AMZN", "cash_capex", year, date(2026, 10, 9), forms=("10-Q", "10-Q/A")
        )
    ]
    receipts = [
        f.model_copy(
            update={
                "id": f.id + "-receipt",
                "metric": "ppe_sale_incentive_proceeds",
                "label": "PP&E出售及激励收款",
                "concept": "amzn:ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives",
                "value": Decimal(20 if f.fiscal_year == 2026 else 5),
            }
        )
        for f in purchases
    ]
    service.corpus.add_facts(filing, receipts)
    return service


def test_stock_agent_retains_prepared_uncited_capex_basis(tmp_path):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.audit import create_package

    class Gateway:
        def complete(self, messages, schemas, review=False):
            context = json.loads(messages[1]["content"])
            if review:
                # Controlled approval isolates package retention, not model accuracy.
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "verdicts": [
                                    {"index": c["index"], "supported": True, "reason": "Fixture"}
                                    for c in context["claims"]
                                ]
                            }
                        )
                    }
                }
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "submit",
                            "type": "function",
                            "function": {
                                "name": "submit_answer",
                                "arguments": json.dumps(
                                    {
                                        "claims": [
                                            {
                                                "kind": "analysis",
                                                "statement": "现金流分析需分开观察经营现金流与现金PP&E购置，投资回报还需其他证据。",
                                                "evidence_ids": [
                                                    context["cashflow_overview"]["current_id"]
                                                ],
                                            }
                                        ],
                                        "gaps": [],
                                    }
                                ),
                            },
                        }
                    ],
                }
            }

    service = net_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 9))
    tools.market = None
    answer = ResearchAgent(replace(service.settings, max_rounds=1), Gateway()).run(
        ResearchRequest(language="zh", question="亚马逊股票怎么样？", ticker="AMZN", as_of="2026-10-09"), tools
    )
    assert answer.answer
    annual = next(t["result"] for t in answer.trace if t.get("tool") == "get_company_overview")
    assert not annual["found"]  # A latest 10-Q survives an unavailable annual overview.
    basis = next(
        t["result"]["capex_basis"] for t in answer.trace if t.get("tool") == "get_cashflow"
    )
    retained = {e["id"] for e in answer.evidence}
    assert {value for key, value in basis.items() if key.endswith("_id") and value} <= retained
    assert not {basis["net_id"], basis["net_growth_id"]} & set(answer.answer[0]["evidence_ids"])
    assert audit_package(create_package(answer, tools.domain, "AMZN"))["status"] == "consistent"


def test_cashflow_exposes_source_bound_gross_net_and_same_basis_growth(tmp_path):
    service = net_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 9))
    result = tools.get_cashflow("AMZN")
    basis = result["capex_basis"]
    assert basis["scope"] == "net_ppe_purchases"
    gross, net = (tools.ledger[basis[k]] for k in ("gross_id", "net_id"))
    assert Decimal(gross["value"]) == 120
    assert Decimal(net["value"]) == 100
    assert net["formula"] == "cash_capex - ppe_sale_incentive_proceeds"
    assert net["input_ids"] == [basis["gross_id"], basis["receipts_id"]]
    assert "净额" in net["label"] and "毛额" in basis["note"]
    growth = tools.ledger[basis["net_growth_id"]]
    assert Decimal(growth["value"]) == (Decimal(100) / 45 - 1) * 100
    assert growth["inputs"][0]["operation"] == "net_cash_capex"
    assert growth["inputs"][1]["operation"] == "net_cash_capex"
    assert "total capital" in basis["note"]


def test_net_purchase_tool_rejects_other_company_or_mixed_source_windows(tmp_path):
    service = net_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 9))
    current = tools.get_cashflow("AMZN")["current_id"]
    gross, receipt = tools.ledger[current]["inputs"][1:]
    tools.ledger[receipt["id"]] = {**receipt, "start": "2026-04-01"}
    with pytest.raises(ValueError, match="same.*period"):
        tools.calculate("net_cash_capex", gross["id"], receipt["id"])


def test_cashflow_research_can_export_explicit_net_purchase_and_recompute(tmp_path):
    service = net_fixture(tmp_path)
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="最新自由现金流和现金资本开支毛额、净额及同比是多少？",
            ticker="AMZN",
            as_of=date(2026, 10, 9),
            mode="reference",
        )
    )
    assert any("购置净额" in c["text"] for c in answer.answer)
    assert len({tuple(c["evidence_ids"]) for c in answer.answer}) == len(answer.answer)
    package = service.export_package(answer.id)
    assert audit_package(package)["status"] == "consistent"
    tools = service.tools(date(2026, 10, 9))
    basis = tools.get_cashflow("AMZN")["capex_basis"]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="calculation", evidence_ids=[basis["net_id"]])]),
        tools,
        "净购置支出是多少？",
        focus_ticker="AMZN",
    )
    assert accepted and not errors, errors


@pytest.mark.parametrize(
    "key,value",
    [
        ("concept", "other:Proceeds"),
        ("ticker", "AAPL"),
        ("unit", "EUR"),
        ("value", "-1"),
        ("filing_id", "other-filing"),
        ("end", "2026-06-29"),
    ],
)
def test_net_capex_rejects_unbound_adjustments(tmp_path, key, value):
    tools = net_fixture(tmp_path).tools(date(2026, 10, 9))
    basis = tools.get_cashflow("AMZN")["capex_basis"]
    tools.ledger[basis["receipts_id"]] = {**tools.ledger[basis["receipts_id"]], key: value}
    with pytest.raises(ValueError):
        tools.calculate("net_cash_capex", basis["gross_id"], basis["receipts_id"])


def test_missing_receipts_are_not_zero_and_requested_net_amount_has_actionable_gap(tmp_path):
    service, _ = cash_fixture(tmp_path, ticker="AMZN", form="10-Q")
    basis = service.tools(date(2026, 10, 9)).get_cashflow("AMZN")["capex_basis"]
    assert basis["scope"] == "gross_ppe_purchases" and basis["net_id"] is None
    assert basis["receipts_id"] is None and basis["net_growth_id"] is None
    request = ResearchRequest(language="zh", question="净资本开支是多少？", ticker="AMZN", as_of=date(2026, 10, 9))
    assert service.research_plan(request)["kind"] == "cashflow"
    answer = service.research(request)
    assert answer.status == "partial"
    assert any("现金流量表" in gap and "缺值" in gap for gap in answer.gaps)
    assert not any("购置净额" in c["text"] for c in answer.answer)


@pytest.mark.parametrize(
    "question",
    [
        "净资本开支是多少？",
        "PP&E购置净额是多少？",
        "PP&E购置毛额、净额是多少？",
        "net PP&E purchases?",
        "TTM net PP&E purchases?",
    ],
)
def test_net_purchase_questions_use_bound_cashflow_tools(tmp_path, question):
    service = net_ttm_fixture(tmp_path) if "TTM" in question else net_fixture(tmp_path)
    request = ResearchRequest(language="zh", question=question, ticker="AMZN", as_of=date(2026, 10, 9))
    plan = service.research_plan(request)
    assert plan["ready"] and plan["kind"] == "cashflow" and not plan["uses_model"]
    answer = service.research(request)
    assert answer.usage["model_calls"] == 0
    assert any("现金PP&E购置净额" in c["text"] for c in answer.answer)


def test_net_growth_does_not_mix_gross_and_net_or_use_negative_prior_base(tmp_path):
    tools = net_fixture(tmp_path).tools(date(2026, 10, 9))
    overview = tools.get_cashflow("AMZN")
    basis = overview["capex_basis"]
    with pytest.raises(ValueError):
        tools.calculate("growth", basis["net_id"], overview["previous_id"])
    prior = tools.ledger[basis["prior_net_id"]]
    receipt = deepcopy(prior["inputs"][1])
    receipt["value"] = "60"
    tools.ledger[receipt["id"]] = receipt
    negative = tools.calculate("net_cash_capex", prior["inputs"][0]["id"], receipt["id"])[
        "evidence"
    ]
    assert Decimal(negative["value"]) == -10
    with pytest.raises(ValueError, match="positive"):
        tools.calculate("growth", basis["net_id"], negative["id"])


@pytest.mark.parametrize(
    "term", ["净资本开支", "PP&E购置净额", "现金PP&E购置净额", "现金 PP&E 购置净额"]
)
def test_net_growth_cannot_be_supported_by_gross_growth_only(tmp_path, term):
    tools = net_fixture(tmp_path).tools(date(2026, 10, 9))
    overview = tools.get_cashflow("AMZN")
    gross_growth = next(
        i
        for i in overview["component_growth_ids"]
        if tools.ledger[i]["inputs"][0]["metric"] == "cash_capex"
    )

    def reviewer(_, claims):
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "controlled reviewer"}
                for c in claims
            ]
        }

    claim = Claim(
        kind="analysis", statement=f"年内累计{term}同比增长。", evidence_ids=[gross_growth]
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert not accepted and any("comparison" in e for e in errors), errors
    claim.evidence_ids = [overview["capex_basis"]["net_growth_id"]]
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert accepted and not errors, errors


def test_gross_purchase_growth_cannot_be_supported_by_net_growth_only(tmp_path):
    tools = net_fixture(tmp_path).tools(date(2026, 10, 9))
    result = tools.get_cashflow("AMZN")

    def reviewer(_, claims):
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "controlled"} for c in claims
            ]
        }

    claim = Claim(
        kind="analysis",
        statement="年内累计现金 PP&E 购置毛额同比增长。",
        evidence_ids=[result["capex_basis"]["net_growth_id"]],
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert not accepted and any("comparison" in e for e in errors), errors
    claim.evidence_ids = [
        next(
            i
            for i in result["component_growth_ids"]
            if tools.ledger[i]["inputs"][0]["metric"] == "cash_capex"
        )
    ]
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert accepted and not errors, errors


def test_relative_growth_requires_both_metrics_and_correct_order(tmp_path):
    tools = net_fixture(tmp_path).tools(date(2026, 10, 9))
    overview = tools.get_cashflow("AMZN")
    growths = {tools.ledger[i]["inputs"][0]["metric"]: i for i in overview["component_growth_ids"]}

    def reviewer(_, claims):
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "controlled reviewer"}
                for c in claims
            ]
        }

    claim = Claim(
        kind="analysis",
        statement="年内累计PP&E购置净额增速快于经营现金流增速。",
        evidence_ids=[growths["cash_capex"], growths["operating_cash_flow"]],
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert not accepted and any("growth comparison" in e for e in errors), errors
    claim.evidence_ids = [overview["capex_basis"]["net_growth_id"], growths["operating_cash_flow"]]
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert accepted and not errors, errors
    claim.statement = "年内累计PP&E购置净额增速慢于经营现金流增速。"
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "现金投入怎么样？", reviewer, "AMZN"
    )
    assert not accepted and any("growth comparison" in e for e in errors), errors


@pytest.mark.parametrize("stem", ["现金投入", "现金PP&E购置", "PP&E购置"])
@pytest.mark.parametrize("complete", [False, True])
def test_coordinated_gross_net_growth_requires_every_comparison(tmp_path, stem, complete):
    tools = net_fixture(tmp_path).tools(date(2026, 10, 9))
    overview = tools.get_cashflow("AMZN")
    growths = {tools.ledger[i]["inputs"][0]["metric"]: i for i in overview["component_growth_ids"]}
    ids = [growths["cash_capex"], growths["operating_cash_flow"]]
    if complete:
        ids.append(overview["capex_basis"]["net_growth_id"])
    seen = []

    def reviewer(question, claims):
        seen.extend(claims)
        return {
            "verdicts": [dict(index=c["index"], supported=True, reason="Fixture") for c in claims]
        }

    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    statement=f"年内累计{stem}毛额与净额增速均快于经营现金流。",
                    evidence_ids=ids,
                )
            ]
        ),
        tools,
        "现金投入怎么样？",
        reviewer,
        "AMZN",
    )
    assert bool(accepted) is complete and bool(seen) is complete, errors
    assert bool(errors) is (not complete)


def net_ttm_fixture(tmp_path):
    service, filings = rolling_fixture(tmp_path, ticker="AMZN")
    for filing in filings:
        with service.corpus.connect() as db:
            facts = [
                Fact.model_validate(json.loads(r["payload"]))
                for r in db.execute("SELECT payload FROM facts WHERE filing_id=?", (filing.id,))
            ]
        receipts = [
            f.model_copy(
                update={
                    "id": f.id + "-receipt",
                    "metric": "ppe_sale_incentive_proceeds",
                    "label": "PP&E出售及激励收款",
                    "concept": "amzn:ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives",
                    "value": Decimal(
                        10 if filing.form == "10-K" else 8 if f.fiscal_year == 2026 else 3
                    ),
                }
            )
            for f in facts
            if f.metric == "cash_capex"
        ]
        service.corpus.ingest(filing.model_copy(update={"cik": "0001018724"}), facts + receipts, [])
    return service


def test_net_ttm_keeps_exact_window_source_decomposition_and_export_audit(tmp_path):
    service = net_ttm_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 9))
    result = tools.get_cashflow("AMZN", "ttm")
    basis = result["capex_basis"]
    net = tools.ledger[basis["net_id"]]
    assert Decimal(net["value"]) == 115  # (80+90-40) - (10+8-3)
    assert period_label(net) == "TTM（2025-07-01 至 2026-06-30）"
    assert basis["net_growth_id"] is None
    assert all(e["operation"] == "trailing_sum" and len(e["inputs"]) == 3 for e in net["inputs"])
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="TTM自由现金流与现金资本开支毛额、净额是多少？",
            ticker="AMZN",
            as_of=date(2026, 10, 9),
            mode="reference",
        )
    )
    assert any(
        "购置净额" in c["text"] and "TTM（2025-07-01 至 2026-06-30）" in c["text"]
        for c in answer.answer
    )
    package = service.export_package(answer.id)
    assert audit_package(package)["status"] == "consistent"
    edited = deepcopy(package)
    altered = next(
        e
        for e in edited["research"]["evidence"]
        if e.get("operation") == "net_cash_capex"
        and e["inputs"][0].get("operation") == "trailing_sum"
    )
    altered["value"] = "116"
    from filingcopilot.audit import digest

    edited["manifest"]["sha256"] = digest(edited["research"])
    assert audit_package(edited)["status"] != "consistent"


def test_net_ttm_rejects_mutated_component_or_misaligned_window(tmp_path):
    tools = net_ttm_fixture(tmp_path).tools(date(2026, 10, 9))
    basis = tools.get_cashflow("AMZN", "ttm")["capex_basis"]
    gross = deepcopy(tools.ledger[basis["gross_id"]])
    gross["value"] = "999"
    tools.ledger[gross["id"]] = gross
    with pytest.raises(ValueError, match="differs"):
        tools.calculate("net_cash_capex", gross["id"], basis["receipts_id"])
