from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from filingcopilot.audit import audit_package, digest
from filingcopilot.ingestion import extract_facts
from filingcopilot.models import Claim, Filing, ResearchRequest, Submission
from filingcopilot.periods import fact_inputs, period_label
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.ttm import ttm_period
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def rolling_fixture(tmp_path, ticker="AAPL"):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    values = [
        ("RevenueFromContractWithCustomerExcludingAssessedTax", (400, 250, 180)),
        ("NetIncomeLoss", (50, 30, 20)),
        ("GrossProfit", (160, 110, 70)),
        ("OperatingIncomeLoss", (80, 60, 35)),
        ("NetCashProvidedByUsedInOperatingActivities", (200, 70, 50)),
        ("PaymentsToAcquirePropertyPlantAndEquipment", (80, 90, 40)),
    ]
    filings = []
    for form, accn, filed, end, periods in [
        ("10-K", "annual", "2026-01-30", "2025-12-31", [("2025-01-01", "2025-12-31", 0)]),
        (
            "10-Q",
            "quarter",
            "2026-07-31",
            "2026-06-30",
            [
                ("2026-01-01", "2026-06-30", 1),
                ("2025-01-01", "2025-06-30", 2),
            ],
        ),
    ]:
        filing = Filing(
            id=ticker + ":" + accn,
            ticker=ticker,
            company="Rolling fixture",
            cik="0000320193",
            accession=accn,
            form=form,
            filed=filed,
            period_end=end,
            source_url="https://www.sec.gov/Archives/" + accn + ".htm",
            content_sha256="a" * 64,
        )
        data = {
            "facts": {
                "us-gaap": {
                    concept: {
                        "units": {
                            "USD": [
                                {
                                    "accn": accn,
                                    "form": form,
                                    "start": s,
                                    "end": e,
                                    "val": amounts[i],
                                }
                                for s, e, i in periods
                            ]
                        }
                    }
                    for concept, amounts in values
                }
            }
        }
        service.corpus.ingest(filing, extract_facts(data, filing), [])
        filings.append(filing)
    service.discovery.prepare_quarterly = lambda *_: {"found": True, "warning": None}
    return service, filings


def test_ttm_cashflow_and_components_keep_three_period_provenance(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_cashflow("AAPL", "ttm")
    assert result["found"] and result["period_scope"] == "ttm"
    assert (result["start"], result["end"]) == ("2025-07-01", "2026-06-30")
    cash = tools.ledger[result["current_id"]]
    assert Decimal(cash["value"]) == 90
    assert len(cash["inputs"]) == 3 and all(
        e["operation"] == "free_cash_flow" for e in cash["inputs"]
    )
    assert [Decimal(tools.ledger[i]["value"]) for i in result["component_ids"]] == [220, 130]
    assert period_label(cash) == "TTM（2025-07-01 至 2026-06-30）"


@pytest.mark.parametrize(
    "question,kind",
    [
        ("TTM自由现金流和现金资本开支是多少？", "cashflow"),
        ("过去十二个月营收和净利润是多少？", "ttm"),
        ("连续12个月经营现金流是多少？", "ttm"),
        ("滚动营收是多少？", "ttm"),
    ],
)
def test_ttm_route_zero_model_and_export_recompute(tmp_path, question, kind):
    service, _ = rolling_fixture(tmp_path)
    request = ResearchRequest(language="zh", question=question, ticker="AAPL")
    plan = service.research_plan(request)
    assert plan["ready"] and plan["kind"] == kind and not plan["uses_model"]
    answer = service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert answer.answer and all(
        "TTM（2025-07-01 至 2026-06-30）" in c["text"] for c in answer.answer
    )
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_ttm_cannot_substitute_annual_or_ytd_claim(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    cash = tools.get_cashflow("AAPL", "ttm")
    annual = tools.ledger[cash["current_id"]]["inputs"][0]
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="calculation", evidence_ids=[annual["id"]])]),
        tools,
        "TTM自由现金流是多少？",
        focus_ticker="AAPL",
    )
    assert not accepted and any("trailing" in e for e in errors)


@pytest.mark.parametrize(
    "field,value,position",
    [
        ("start", "2026-01-02", 1),
        ("start", "2025-01-02", 2),
        ("end", "2025-05-31", 2),
        ("unit", "EUR", 1),
        ("ticker", "MSFT", 2),
        ("metric", "diluted_eps", 0),
        ("filing_id", "other-version", 2),
    ],
)
def test_ttm_guard_rejects_calendar_currency_metric_and_version_mix(
    tmp_path, field, value, position
):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_ttm_financial_fact("AAPL", "revenue")["evidence"]
    inputs = deepcopy(result["inputs"])
    inputs[position][field] = value
    with pytest.raises(ValueError):
        ttm_period(inputs)


def test_ttm_fiscal_week_calendar_is_preserved(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    inputs = deepcopy(tools.get_ttm_financial_fact("AAPL", "revenue")["evidence"]["inputs"])
    for e, s, end in zip(
        inputs,
        ("2024-09-29", "2025-09-28", "2024-09-29"),
        ("2025-09-27", "2026-06-27", "2025-06-28"),
    ):
        e.update(start=s, end=end)
    assert ttm_period(inputs) == ("2025-06-29", "2026-06-27")


def test_ttm_missing_prior_capex_does_not_zero_fill(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute(
            "DELETE FROM facts WHERE metric='cash_capex' AND fiscal_year=2025 AND filing_id='AAPL:quarter'"
        )
    result = service.tools(date(2026, 10, 8)).get_cashflow("AAPL", "ttm")
    assert not result["found"] and "上年同期" in result["reason"]


def test_ttm_gross_and_net_formulas_cannot_mix(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    inputs = deepcopy(tools.ledger[tools.get_cashflow("AAPL", "ttm")["current_id"]]["inputs"])
    inputs[1]["formula"] += " + ppe_sale_incentive_proceeds"
    with pytest.raises(ValueError, match="identical"):
        ttm_period(inputs)


def test_ttm_only_annual_available_uses_actual_full_year_window(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 7, 30))
    result = tools.get_ttm_financial_fact("AAPL", "revenue")
    assert result["found"] and result["warnings"]
    assert len(result["evidence"]["inputs"]) == 1
    assert period_label(result["evidence"]) == "TTM（2025-01-01 至 2025-12-31）"
    cash = tools.get_cashflow("AAPL", "ttm")
    assert cash["found"] and len(tools.ledger[cash["current_id"]]["inputs"]) == 1


def test_ttm_future_source_rejected_even_if_inserted_in_ledger(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_ttm_financial_fact("AAPL", "revenue")["evidence"]
    tools.as_of = date(2026, 7, 30)
    with pytest.raises(ValueError, match="knowledge"):
        tools.calculate("trailing_sum", *result["input_ids"])


def test_ttm_value_tampering_detected_with_rebuilt_checksum(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    answer = service.research(ResearchRequest(language="zh", question="TTM自由现金流是多少？", ticker="AAPL"))
    package = service.export_package(answer.id)
    evidence = package["research"]["evidence"]
    result = next(
        e
        for e in evidence
        if e.get("operation") == "trailing_sum" and e["inputs"][0]["kind"] == "calculation"
    )
    result["value"] = "999"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


@pytest.mark.parametrize("question", ["TTM总资产是多少？"])
def test_non_additive_ttm_questions_have_honest_alternatives(tmp_path, question):
    service, _ = rolling_fixture(tmp_path)
    plan = service.research_plan(ResearchRequest(language="zh", question=question, ticker="AAPL"))
    assert not plan["ready"] and plan["suggestions"] and "TTM" in plan["message"]


def test_ttm_missing_network_keeps_cached_facts_with_freshness_warning(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    service.discovery.prepare_quarterly = lambda *_: {
        "found": True,
        "warning": "fixture: 最新季报检查失败，使用缓存",
    }
    answer = service.research(ResearchRequest(language="zh", question="TTM营收是多少？", ticker="AAPL"))
    assert answer.status == "partial" and answer.answer
    assert any("检查失败" in g for g in answer.gaps)


def test_malformed_ttm_arity_is_an_invalid_package_not_a_crash(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    answer = service.research(ResearchRequest(language="zh", question="TTM营收是多少？", ticker="AAPL"))
    package = service.export_package(answer.id)
    item = next(e for e in package["research"]["evidence"] if e.get("operation") == "trailing_sum")
    item["inputs"] = item["inputs"][:2]
    item["input_ids"] = item["input_ids"][:2]
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


def test_ttm_fiscal_year_ambiguity_is_resolved_before_running(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    plan = service.research_plan(ResearchRequest(language="zh", question="2025 TTM营收是多少？", ticker="AAPL"))
    assert not plan["ready"] and "日期" in plan["message"]


def test_ttm_analysis_can_reference_only_its_derived_window(tmp_path):
    from filingcopilot.verification import _without_source_dates

    service, _ = rolling_fixture(tmp_path)
    item = service.tools(date(2026, 10, 8)).get_ttm_financial_fact("AAPL", "revenue")["evidence"]
    assert _without_source_dates("2025年7月1日至2026年6月30日", [item]) == "至"
    assert "2025年7月2日" in _without_source_dates("2025年7月2日收入10美元", [item])
    assert "10美元" in _without_source_dates("2025年7月1日收入10美元", [item])


def test_transition_fiscal_year_is_missing_ttm_not_an_exception(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute(
            "UPDATE facts SET payload=json_set(payload,'$.start','2025-02-01') WHERE filing_id='AAPL:annual'"
        )
    tools = service.tools(date(2026, 7, 30))
    assert not tools.get_ttm_financial_fact("AAPL", "revenue")["found"]
    cash = tools.get_cashflow("AAPL", "ttm")
    assert not cash["found"] and "过渡" in cash["reason"]


@pytest.mark.parametrize(
    "ratio,profit", [("gross_margin", 200), ("operating_margin", 105), ("net_margin", 60)]
)
def test_ttm_margin_is_ratio_of_aligned_flows_not_sum_of_percentages(tmp_path, ratio, profit):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_ttm_margin("AAPL", ratio)
    assert result["found"] and (result["start"], result["end"]) == ("2025-07-01", "2026-06-30")
    item = result["evidence"]
    assert Decimal(item["value"]) == Decimal(profit) / 470 * 100
    assert [Decimal(e["value"]) for e in item["inputs"]] == [profit, 470]
    assert all(e["operation"] == "trailing_sum" and len(e["inputs"]) == 3 for e in item["inputs"])
    assert period_label(item) == "TTM（2025-07-01 至 2026-06-30）"


def test_ttm_three_margins_route_verify_and_export(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    request = ResearchRequest(
        language="zh",
        question="TTM毛利率、营业利润率和净利率是多少？", ticker="AAPL", as_of="2026-10-08"
    )
    plan = service.research_plan(request)
    assert plan["ready"] and not plan["uses_model"] and plan["kind"] == "ttm"
    answer = service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert len(answer.answer) == 3 and all(
        "TTM（2025-07-01 至 2026-06-30）" in c["text"] for c in answer.answer
    )
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize(
    "change", ["ticker", "unit", "annual_version", "quarter_version", "window", "value"]
)
def test_ttm_margin_rejects_mismatched_sources_even_when_each_flow_is_valid(tmp_path, change):
    from datetime import timedelta

    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    left = tools.get_ttm_financial_fact("AAPL", "net_income")["evidence"]
    right = tools.get_ttm_financial_fact("AAPL", "revenue")["evidence"]
    inputs = deepcopy(right["inputs"])
    for i, item in enumerate(inputs):
        if change in ("ticker", "unit"):
            item[change] = "MSFT" if change == "ticker" else "EUR"
            if change == "ticker":
                item["filing"]["ticker"] = "MSFT"
        if change == "annual_version" and i == 0 or change == "quarter_version" and i > 0:
            item["filing_id"] += "-amended"
            item["filing"]["id"] = item["filing_id"]
        if change == "window":
            for key in ("start", "end"):
                item[key] = (date.fromisoformat(item[key]) + timedelta(days=1)).isoformat()
    from filingcopilot.ttm import trailing_calculation

    altered = trailing_calculation(tools, inputs)["evidence"]
    if change == "value":
        altered["value"] = "999"
    with pytest.raises(ValueError, match="TTM margin"):
        tools.calculate("ratio", left["id"], altered["id"])


@pytest.mark.parametrize("revenue", [0, -10])
def test_ttm_margin_nonpositive_revenue_is_missing_not_infinite(tmp_path, revenue):
    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute(
            "UPDATE facts SET payload=json_set(payload,'$.value',?) WHERE metric='revenue'",
            (str(revenue),),
        )
    result = service.tools(date(2026, 10, 8)).get_ttm_margin("AAPL", "net_margin")
    assert not result["found"] and "收入为零或负值" in result["reason"]


def test_ttm_negative_profit_has_negative_margin(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute(
            "UPDATE facts SET payload=json_set(payload,'$.value','-50') WHERE metric='net_income' AND filing_id='AAPL:annual'"
        )
    result = service.tools(date(2026, 10, 8)).get_ttm_margin("AAPL", "net_margin")
    assert result["found"] and Decimal(result["evidence"]["value"]) == Decimal(-40) / 470 * 100


def test_ttm_margin_missing_profit_is_partial_without_zero_fill(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute("DELETE FROM facts WHERE metric='gross_profit' AND filing_id='AAPL:annual'")
    answer = service.research(
        ResearchRequest(language="zh", question="TTM毛利率和净利率是多少？", ticker="AAPL", as_of="2026-10-08")
    )
    assert answer.status == "partial" and len(answer.answer) == 1
    assert "净利率" in answer.answer[0]["text"] and answer.gaps
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_ttm_margin_annual_fallback_and_date_cutoff(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 7, 30))
    result = tools.get_ttm_margin("AAPL", "net_margin")
    assert result["found"] and result["warnings"]
    assert period_label(result["evidence"]) == "TTM（2025-01-01 至 2025-12-31）"
    assert Decimal(result["evidence"]["value"]) == Decimal("12.5")
    current = service.tools(date(2026, 10, 8))
    item = current.get_ttm_margin("AAPL", "net_margin")["evidence"]
    current.as_of = date(2026, 7, 30)
    with pytest.raises(ValueError, match="knowledge"):
        current.calculate("ratio", *item["input_ids"])


def test_annual_ratio_cannot_answer_ttm_margin_and_wrong_margin_rejected(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    annual = tools.calculate(
        "ratio",
        tools.get_financial_fact("AAPL", "net_income", 2025)["evidence"]["id"],
        tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]["id"],
    )["evidence"]
    gross = tools.get_ttm_margin("AAPL", "gross_margin")["evidence"]
    for item in (annual, gross):
        accepted, errors = verify_submission(
            Submission(claims=[Claim(kind="calculation", evidence_ids=[item["id"]])]),
            tools,
            "TTM净利率是多少？",
            focus_ticker="AAPL",
        )
        assert not accepted and errors


@pytest.mark.parametrize("mutation", ["value", "formula", "input_order", "input_type"])
def test_ttm_margin_tampering_rejected_after_checksum_rebuilt(tmp_path, mutation):
    service, _ = rolling_fixture(tmp_path)
    answer = service.research(
        ResearchRequest(language="zh", question="TTM净利率是多少？", ticker="AAPL", as_of="2026-10-08")
    )
    package = service.export_package(answer.id)
    item = next(e for e in package["research"]["evidence"] if e.get("operation") == "ratio")
    if mutation in ("value", "formula"):
        item[mutation] = "999" if mutation == "value" else "quarterly percentages added"
    elif mutation == "input_order":
        item["inputs"].reverse()
        item["input_ids"].reverse()
    else:
        item["inputs"][0] = item["inputs"][0]["inputs"][0]
        item["input_ids"][0] = item["inputs"][0]["id"]
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


def test_ttm_margin_analysis_needs_comparable_history_and_can_use_derived_dates(tmp_path):
    from filingcopilot.verification import _without_source_dates, analysis_conflict

    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    item = tools.get_ttm_margin("AAPL", "net_margin")["evidence"]
    assert _without_source_dates("2025年7月1日至2026年6月30日", [item]) == "至"
    assert analysis_conflict("净利率上升。", [item], tools.domain)


class TrailingStockGateway:
    """Controlled approval exercises source rules, not model accuracy."""

    def __init__(self):
        self.context = None

    def complete(self, messages, tools, review=False):
        import json

        if review:
            claims = json.loads(messages[1]["content"])["claims"]
            message = {
                "content": json.dumps(
                    {
                        "verdicts": [
                            {"index": c["index"], "supported": True, "reason": "Controlled review"}
                            for c in claims
                        ]
                    }
                )
            }
        else:
            request = json.loads(messages[1]["content"])
            self.context = request.get("trailing_overview")
            if self.context:
                evidence = next(e for e in self.context["evidence"] if e["metric"] == "revenue")
                statement = "已取得滚动期间营收，为经营规模提供依据；同业估值仍需核验。"
            else:
                evidence = request["annual_overview"]["evidence"][0]
                statement = "已取得年度营收，为经营规模提供依据；同业估值仍需核验。"
            message = {
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
                                            "statement": statement,
                                            "evidence_ids": [evidence["id"]],
                                        },
                                        {
                                            "kind": "analysis",
                                            "statement": "现有营收信息不能单独判断股票贵贱，仍需估值基准。",
                                            "evidence_ids": [evidence["id"]],
                                        },
                                    ],
                                    "gaps": ["TTM 自由现金流同比缺少更早窗口。"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
        return {"message": message, "usage": {"total_tokens": 10}}


def trailing_stock_answer(service, as_of="2026-10-08", question="苹果股票怎么样？"):
    from filingcopilot.agent import ResearchAgent

    tools = service.tools(date.fromisoformat(as_of))
    tools.market = None
    gateway = TrailingStockGateway()
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(language="zh", question=question, ticker="AAPL", as_of=as_of), tools
    )
    return answer, gateway, tools


def test_stock_prefetch_retains_uncited_ttm_calculations_and_audits_inputs(tmp_path):
    from filingcopilot.audit import create_package

    service, _ = rolling_fixture(tmp_path)
    answer, gateway, tools = trailing_stock_answer(service)
    expected = {
        "revenue": Decimal(470),
        "operating_income": Decimal(105),
        "net_income": Decimal(60),
        "operating_cash_flow": Decimal(220),
        "free_cash_flow": Decimal(90),
        "operating_margin": Decimal(105) / 470 * 100,
        "net_margin": Decimal(60) / 470 * 100,
    }
    prepared = gateway.context["evidence"]
    assert {e["metric"]: Decimal(e["value"]) for e in prepared} == expected
    assert all((e["start"], e["end"]) == ("2025-07-01", "2026-06-30") for e in prepared)
    assert all("inputs" not in e for e in prepared)
    assert answer.answer[0]["kind"] == "analysis" and answer.usage["model_calls"] == 2
    assert len(answer.answer) == 2  # Prepared figures do not inflate submitted claims.
    assert {e["id"] for e in prepared} <= {e["id"] for e in answer.evidence}
    assert "TTM 自由现金流同比缺少更早窗口。" in answer.gaps
    package = create_package(answer, tools.domain, "AAPL")
    audit = audit_package(package, corpus=tools.corpus, trusted_domain=tools.domain)
    assert audit["status"] == "consistent" and audit["source_validation"] == "matched"
    assert audit["narrative_claims_not_rechecked"] == 2
    uncited = next(e for e in package["research"]["evidence"] if e["label"] == "净利润")
    uncited["value"] = "999"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


def test_stock_ttm_prefetch_respects_cutoff_and_annual_fallback(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    answer, gateway, _ = trailing_stock_answer(service, as_of="2026-07-30")
    assert gateway.context["warnings"]
    assert all(
        (e["start"], e["end"]) == ("2025-01-01", "2025-12-31") for e in gateway.context["evidence"]
    )
    assert all(
        len(e["inputs"]) == 1 for e in answer.evidence if e.get("operation") == "trailing_sum"
    )
    assert all(
        date.fromisoformat(f["filing"]["filed"]) <= answer.as_of
        for e in answer.evidence
        for f in fact_inputs(e)
    )
    assert any("不延伸到今天" in g for g in answer.gaps)


def test_stock_requested_year_does_not_get_current_ttm(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    answer, gateway, _ = trailing_stock_answer(service, question="苹果FY2025股票表现怎么样？")
    assert gateway.context is None
    assert not any(e.get("operation") == "trailing_sum" for e in answer.evidence)
    assert not any(t["stage"] == "ttm_preparation" for t in answer.trace)


def test_stock_ttm_missing_profit_does_not_erase_other_flows(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute(
            "DELETE FROM facts WHERE metric='net_income' AND filing_id='AAPL:quarter' AND fiscal_year=2025"
        )
    answer, gateway, _ = trailing_stock_answer(service)
    metrics = {e["metric"] for e in gateway.context["evidence"]}
    assert "net_income" not in metrics and "net_margin" not in metrics
    assert {"revenue", "operating_margin", "free_cash_flow"} <= metrics
    assert any("TTM net_income" in gap for gap in answer.gaps)
    assert all(e["value"] != "0" for e in gateway.context["evidence"])


@pytest.mark.parametrize("failure", [ValueError, OSError])
def test_stock_ttm_source_failure_keeps_other_verified_metrics(tmp_path, monkeypatch, failure):
    from filingcopilot.fundamentals import stock_trailing_context

    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    original = tools.get_ttm_financial_fact

    def get_fact(ticker, metric):
        if metric == "net_income":
            raise failure("原始净利润资料读取失败，请重新获取来源")
        return original(ticker, metric)

    monkeypatch.setattr(tools, "get_ttm_financial_fact", get_fact)
    context = stock_trailing_context(tools, "AAPL")
    assert context["found"] and any("重新获取来源" in gap for gap in context["missing"])
    assert "net_margin" not in {e["metric"] for e in context["evidence"]}
    assert "free_cash_flow" in {e["metric"] for e in context["evidence"]}


def test_source_bound_half_year_adjacent_to_fcf_is_a_date_not_an_amount(tmp_path):
    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    cash = tools.get_cashflow("AAPL", "latest")

    def reviewer(question, claims):
        return {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "Controlled review"}
                for c in claims
            ]
        }

    claim = Claim(
        kind="analysis",
        evidence_ids=[cash["current_id"]],
        statement="2026上半年FCF为负，资本投入的回报仍需核验。",
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果股票怎么样？", reviewer, "AAPL"
    )
    assert accepted and not errors
    for suffix in ("FCF10亿美元", "USD", "倍"):
        claim.statement = f"2026上半年{suffix}。"
        accepted, errors = verify_submission(
            Submission(claims=[claim]), tools, "苹果股票怎么样？", reviewer, "AAPL"
        )
        assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize("annual_capex,sign", [(80, "为正"), (170, "为零"), (240, "为负")])
def test_prepared_ttm_cash_observation_uses_current_sign_without_inventing_transition(
    tmp_path, annual_capex, sign
):
    from filingcopilot.fundamentals import stock_trailing_context

    service, _ = rolling_fixture(tmp_path)
    with service.corpus.connect() as db:
        db.execute(
            "UPDATE facts SET payload=json_set(payload,'$.value',?) WHERE metric='cash_capex' AND filing_id='AAPL:annual'",
            (str(annual_capex),),
        )
    tools = service.tools(date(2026, 10, 8))
    context = stock_trailing_context(tools, "AAPL")
    observation = context["observations"][0]
    assert "经营现金流为正" in observation["statement"]
    assert "项目自由现金流" + sign in observation["statement"]
    assert not any(word in observation["statement"] for word in ("转负", "转正", "同比"))
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", **observation)]),
        tools,
        "苹果股票怎么样？",
        lambda q, claims: {
            "verdicts": [
                {"index": c["index"], "supported": True, "reason": "Controlled review"}
                for c in claims
            ]
        },
        "AAPL",
    )
    assert accepted and not errors


class IncompleteStockGateway(TrailingStockGateway):
    def __init__(self, outcome):
        super().__init__()
        self.outcome, self.reviews = outcome, 0
        self.original_statement = None

    def complete(self, messages, schemas, review=False):
        import json

        if not review and self.outcome == "network":
            raise TimeoutError("Controlled provider failure")
        result = super().complete(messages, schemas, review=review)
        if review:
            self.reviews += 1
            claims = json.loads(messages[1]["content"])["claims"]
            verdicts = json.loads(result["message"]["content"])["verdicts"]
            for claim, verdict in zip(claims, verdicts, strict=True):
                verdict["supported"] = self.outcome == "accept" or (
                    self.outcome == "retain" and claim["statement"] == self.original_statement
                )
            result["message"]["content"] = json.dumps({"verdicts": verdicts})
        else:
            call = result["message"]["tool_calls"][0]["function"]
            draft = json.loads(call["arguments"])
            self.original_statement = draft["claims"][0]["statement"]
            invalid = {**draft["claims"][0], "statement": "目标价格为250美元。"}
            draft["claims"] = (
                [draft["claims"][0], invalid] if self.outcome == "retain" else [invalid]
            )
            call["arguments"] = json.dumps(draft, ensure_ascii=False)
        return result


@pytest.mark.parametrize("outcome", ["accept", "reject", "retain", "network"])
def test_stock_basic_brief_is_reviewed_and_never_forces_rejected_or_unavailable_review(
    tmp_path, outcome
):
    from filingcopilot.agent import ResearchAgent
    from filingcopilot.audit import create_package

    service, _ = rolling_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    tools.market = None
    gateway = IncompleteStockGateway(outcome)
    answer = ResearchAgent(service.settings, gateway).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL", as_of="2026-10-08"), tools
    )
    delivery = [t for t in answer.trace if t["stage"] == "basic_stock_brief"]
    analyses = [c for c in answer.answer if c["kind"] == "analysis"]
    if outcome == "accept":
        assert len(analyses) >= 2 and delivery[0]["accepted_for_delivery"]
        assert all(c["verification"] == "inference_review" for c in analyses)
        assert any("基础研究简报" in gap for gap in answer.gaps)
        assert (
            audit_package(
                create_package(answer, tools.domain, "AAPL"),
                corpus=tools.corpus,
                trusted_domain=tools.domain,
            )["status"]
            == "consistent"
        )
    elif outcome == "network":
        assert not delivery and gateway.reviews == 0 and not analyses
    elif outcome == "retain":
        assert len(analyses) == 1 and analyses[0]["text"] == gateway.original_statement
        assert not delivery
    else:
        assert not analyses and delivery and not delivery[0]["accepted_for_delivery"]
    assert not any("250" in c["text"] for c in answer.answer)
