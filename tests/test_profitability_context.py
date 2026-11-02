"""Margin explanations must use comparable amounts, not rounded display ratios."""
from decimal import Decimal

import pytest

from filingcopilot.agent_execution import ExecutionMixin
from filingcopilot.models import Fact, Filing


@pytest.mark.parametrize("statement,source,accepted", [
    ("营业费用占净销售额的比例与上年持平。", "2025 2024 Operating expenses 62,151 57,467 Percentage of net sales 15% 15%", False),
    ("The operating expense ratio was unchanged from the prior year.", "2025 2024 Operating expenses 62,151 57,467 Percentage of net sales 15% 15%", False),
    ("按表格四舍五入后的展示精度，营业费用占净销售额的比例与上年持平。", "2025 2024 Operating expenses 62,151 57,467 Percentage of net sales 15% 15%", True),
    ("At the rounded display precision, the operating expense ratio was unchanged.", "2025 2024 Operating expenses 62,151 57,467 Percentage of net sales 15% 15%", True),
    ("Operating expenses increased from the prior year.", "2025 2024 Operating expenses 62,151 57,467 Percentage of net sales 15% 15%", True),
    ("The gross margin was unchanged.", "The gross margin remained unchanged at 40% in both years.", True),
])
def test_display_percentages_cannot_establish_exact_flat_ratio(tools, statement, source, accepted):
    from filingcopilot.models import Claim, Submission
    from filingcopilot.verification import verify_submission
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of), "text": source}
    called = []
    def reviewer(question, claims):
        called.extend(claims)
        return {"verdicts": [{"index": c["index"], "supported": True, "reason": "Overpermissive fixture"} for c in claims]}
    result, errors = verify_submission(Submission(claims=[Claim(kind="narrative", statement=statement,
                                                                evidence_ids=["p1"])]), tools,
                                        "Explain the change in profitability.", reviewer)
    assert bool(result) is accepted
    if not accepted:
        assert not called and any("precision" in e for e in errors)


def profitability_facts(tools):
    filing = Filing.model_validate(tools.corpus.evidence("p1", tools.as_of)["filing"])
    facts = []
    for year, revenue, gross, operating in [(2025, 416161, 195201, 133050),
                                           (2024, 391035, 180683, 123216)]:
        for metric, value in [("revenue", revenue), ("gross_profit", gross), ("operating_income", operating)]:
            facts.append(Fact(id=f"amount-{metric}-{year}", filing_id=filing.id, ticker="AAPL",
                              metric=metric, label=tools.domain.metrics[metric].label,
                              concept="us-gaap:Test", value=value, unit="USD", fiscal_year=year,
                              start=f"{year-1}-10-01", end=f"{year}-09-27",
                              source_url=filing.source_url, locator="statement amounts"))
    # Replace fixture facts rather than introduce competing values.
    tools.corpus.ingest(filing, facts, [])
    return [tools.corpus.evidence(f.id, tools.as_of) for f in facts]


def test_margin_comparison_is_prepared_before_drafting(tools, monkeypatch):
    facts = profitability_facts(tools)
    lookup = {(f["metric"], f["fiscal_year"]): f for f in facts}
    def lookup_fact(ticker, metric, fiscal_year):
        f = lookup[(metric, fiscal_year)]
        tools.ledger[f["id"]] = f
        return {"found": True, "evidence": f}
    monkeypatch.setattr(tools, "get_financial_fact", lookup_fact)
    context, trace = ExecutionMixin._prepare_annual_comparison(
        tools, "Why did the FY2025 operating margin change?", "AAPL", 20)
    assert context, "Margin questions need their operands before authoring"
    operations = [e for e in context["evidence"] if e["kind"] == "calculation"]
    assert len([e for e in operations if e["operation"] == "ratio"]) == 4
    assert {e["inputs"][0]["metric"] for e in operations if e["operation"] == "growth"} >= {
        "revenue", "operating_income"}
    assert len(trace) <= 15, "Leave at least five calls for narrative search and submission"


def test_rounded_equal_percentages_do_not_erase_a_real_spread_increase(tools):
    from filingcopilot.profitability import profitability_context
    facts = profitability_facts(tools)
    rows = profitability_context(facts)
    current = next(r for r in rows if r["fiscal_year"] == 2025)
    prior = next(r for r in rows if r["fiscal_year"] == 2024)
    a, b = [Decimal(r["gross_to_operating_spread_percent"]) for r in [current, prior]]
    assert round(a) == round(b) == 15
    assert a > b and Decimal("0.23") < a-b < Decimal("0.25")
    assert Decimal(current["gross_margin_percent"]) > Decimal(prior["gross_margin_percent"])
    assert {x["id"] for x in current["inputs"]} == {f["id"] for f in facts if f["fiscal_year"] == 2025}
    assert "other operating" in current["scope_note"].lower()


@pytest.mark.parametrize("statement,valid", [
    ("营业利润增速快于收入增速，即收入增长带来的利润增量超过同期费用增长。", False),
    ("Operating income grew faster than revenue; therefore the profit increment exceeded the increase in expenses.", False),
    ("营业利润增速快于收入增速，因此营业利润率上升。", True),
    ("Operating income grew faster than revenue, so the operating margin increased.", True),
])
def test_growth_rate_order_does_not_establish_absolute_increment_order(tools, statement, valid):
    from filingcopilot.models import Claim, Submission
    from filingcopilot.verification import verify_submission
    facts = profitability_facts(tools)
    tools.ledger.update({f["id"]: f for f in facts})
    ids = []
    for metric in ("operating_income", "revenue"):
        ids.append(tools.calculate("growth", f"amount-{metric}-2025", f"amount-{metric}-2024")["evidence"]["id"])
    for year in (2025, 2024):
        ids.append(tools.calculate("ratio", f"amount-operating_income-{year}", f"amount-revenue-{year}")["evidence"]["id"])
    def reviewer(question, claims):
        return {"verdicts": [{"index": c["index"], "supported": True, "reason": "Overpermissive review"} for c in claims]}
    result, errors = verify_submission(Submission(claims=[Claim(kind="analysis", statement=statement,
                                                                evidence_ids=ids)]), tools,
                                        "Why did operating margin change in FY2025?", reviewer)
    assert bool(result) is valid
    if not valid:
        assert any("amount" in e.casefold() or "increment" in e.casefold() for e in errors)


@pytest.mark.parametrize("statement,valid,source_present", [
    ("毛利率至营业利润率的差额还包含其他经营项目。", False, False),
    ("The gross-to-operating spread includes other operating items.", False, False),
    ("毛利率至营业利润率的差额可能包含其他经营项目，现有计算不能证明其具体构成。", True, False),
    ("The gross-to-operating spread may include other operating items; the calculations alone do not establish its composition.", True, False),
    ("The gross-to-operating spread includes other operating items.", True, True),
])
def test_arithmetic_spread_does_not_establish_other_line_item_composition(tools, statement, valid, source_present):
    from filingcopilot.models import Claim, Submission
    from filingcopilot.verification import verify_submission
    source = tools.corpus.evidence("p1", tools.as_of)
    facts = profitability_facts(tools)
    tools.ledger.update({f["id"]: f for f in facts})
    ids = [tools.calculate("ratio", f"amount-{metric}-{year}", f"amount-revenue-{year}")["evidence"]["id"]
           for metric in ("gross_profit", "operating_income") for year in (2025, 2024)]
    if source_present:
        tools.ledger["p1"] = {**source, "text":
                              "Operating income includes gross profit less operating expenses plus other operating income."}
        ids.append("p1")
    def reviewer(question, claims):
        return {"verdicts": [{"index": c["index"], "supported": True, "reason": "Overpermissive review"} for c in claims]}
    result, errors = verify_submission(Submission(claims=[Claim(kind="analysis", statement=statement,
                                                                evidence_ids=ids)]), tools,
                                        "Explain the operating-margin change.", reviewer)
    assert bool(result) is valid
    if not valid:
        assert any("composition" in e.casefold() or "classification" in e.casefold() for e in errors)


@pytest.mark.parametrize("mutation", ["currency", "filing", "period", "conflicting_value", "missing"])
def test_profitability_context_never_mix_matches_inputs(tools, mutation):
    from filingcopilot.profitability import profitability_context
    facts = profitability_facts(tools)[:3]
    if mutation == "currency":
        facts[0] = {**facts[0], "unit": "EUR"}
    elif mutation == "filing":
        facts[0] = {**facts[0], "filing_id": "other"}
    elif mutation == "period":
        facts[0] = {**facts[0], "end": "2025-06-27"}
    elif mutation == "conflicting_value":
        facts.append({**facts[0], "id": "other", "value": "1"})
    elif mutation == "missing":
        facts.pop()
    assert not profitability_context(facts)


def test_margin_preparation_respects_small_tool_budget(tools):
    context, trace = ExecutionMixin._prepare_annual_comparison(
        tools, "Why did the FY2025 operating margin change?", "AAPL", 5)
    assert not context and not trace


@pytest.mark.parametrize("question", [
    "公司的 FY2025 利润率变化由哪些已披露因素解释？",
    "What explains the FY2025 change in margins?",
    "Explain the FY2025 change in operating margin.",
])
def test_real_agent_entry_prepares_margin_evidence_for_causal_wording(tools, monkeypatch, question):
    import json

    from filingcopilot.agent import ResearchAgent
    from filingcopilot.models import ResearchRequest
    from filingcopilot.settings import Settings

    facts = profitability_facts(tools)
    lookup = {(f["metric"], f["fiscal_year"]): f for f in facts}
    def lookup_fact(ticker, metric, fiscal_year):
        f = lookup[(metric, fiscal_year)]
        tools.ledger[f["id"]] = f
        return {"found": True, "evidence": f}
    monkeypatch.setattr(tools, "get_financial_fact", lookup_fact)
    contexts = []
    class Gateway:
        def complete(self, messages, schemas, review=False):
            contexts.append(json.loads(messages[1]["content"]))
            return {"message": {"content": "No draft"}, "model": "fixture"}
    answer = ResearchAgent(Settings(home=tools.corpus.home, embedding_mode="off", max_rounds=1,
                                    max_tool_calls=20, min_model_interval=0), Gateway()).run(
        ResearchRequest(question=question, ticker="AAPL", as_of=tools.as_of, language="en"), tools)
    assert contexts
    comparison = contexts[0].get("annual_comparison")
    assert comparison, "The actual drafting entry must receive the computation, not just a helper test"
    assert len(comparison["profitability_reconciliation"]) == 2
    assert any(t["stage"] == "annual_comparison_preparation" for t in answer.trace)
