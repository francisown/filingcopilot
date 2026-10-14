import pytest

from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.polarity import causal_period_conflict, contradiction, directions


def test_free_cash_flow_and_operating_cash_flow_have_separate_directions():
    text = "自由现金流恶化，但经营活动现金流增长。"
    observed = directions(text, DEFAULT_DOMAIN, set())
    assert observed["free_cash_flow"] == {-1}
    assert observed["operating_cash_flow"] == {1}
    assert contradiction(text, ["Operating cash flow increased."], DEFAULT_DOMAIN, set()) is None
    assert contradiction("自由现金流增长。", ["Free cash flow decreased."], DEFAULT_DOMAIN, set())


def test_ttm_causal_window_survives_mixed_quarter_and_ytd_source():
    source = "Operating cash flow increased for the six months ended June 30. The increase in operating cash flow for the trailing twelve months was due to net income and working capital changes."
    claim = "同期经营现金流增长主要来源于净利润与营运资本变动。"
    assert causal_period_conflict(claim, [source], DEFAULT_DOMAIN)
    assert causal_period_conflict("连续十二个月" + claim, [source], DEFAULT_DOMAIN) is None
    assert causal_period_conflict("经营现金流增长。", [source], DEFAULT_DOMAIN) is None
    assert (
        causal_period_conflict(
            claim,
            [source + " The increase in operating cash flow for six months was due to net income."],
            DEFAULT_DOMAIN,
        )
        is None
    )


@pytest.mark.parametrize(
    "source,claim",
    [
        ("Gross margin grew to $10 billion. Gross margin percentage fell.", "毛利率提升。"),
        ("Net income declined due to weaker demand.", "净利润增长。"),
        ("营业成本下降，营业收入上升。", "营业成本增加。"),
        ("Operating cash flow decreased.", "经营活动现金流增长。"),
        ("Total assets rose.", "总资产减少。"),
    ],
)
def test_bound_metric_direction_rejects_contradictions(source, claim):
    assert contradiction(claim, [source], DEFAULT_DOMAIN, set())


@pytest.mark.parametrize(
    "source,claim",
    [
        ("Revenue declined while net income increased.", "净利润增长。"),
        ("Revenue did not decline.", "营业收入上升。"),
        ("Revenue could decrease next year.", "营业收入上升。"),
        ("FY2024 revenue declined. FY2025 revenue increased.", "FY2025 营业收入上升。"),
        ("Revenue increased in one segment but decreased in another.", "营业收入上升。"),
        ("Gross margin grew to $10 billion. Gross margin percentage fell.", "毛利润增长。"),
        ("Revenue declined due to competition.", "后续需要观察营业收入增长是否能延续。"),
        ("Revenue decreased.", "Assess whether revenue growth can continue."),
    ],
)
def test_ambiguity_negation_and_other_metrics_are_left_to_semantic_review(source, claim):
    assert contradiction(claim, [source], DEFAULT_DOMAIN, {2025}) is None
