"""A prose limitation cannot turn a missing requested explanation into a full answer."""

import pytest

from filingcopilot.semantic_boundaries import nonblocking_gap


@pytest.mark.parametrize("question,gap", [
    ("为什么营业利润率下降？", "已给出费用与毛利变动，但尚缺解释二者如何形成净变化的证据。"),
    ("Why did operating margin fall?", "The supported answer lacks the link explaining the net margin movement."),
    ("What supply and regulatory risks are disclosed?", "Only supply risk is covered; regulatory evidence is missing."),
    ("供应和监管风险有哪些？", "无法确认监管风险是否已得到回答。"),
])
def test_required_omission_is_not_a_generic_narrative_caveat(question, gap):
    assert not nonblocking_gap(question, gap)


@pytest.mark.parametrize("question,gap", [
    ("What supply risks are disclosed?", "The filing does not quantify the probability of each risk."),
    ("为什么营业利润变化？", "各因素未单独量化；不影响已给出的定性解释。"),
])
def test_optional_quantification_remains_a_note(question, gap):
    assert nonblocking_gap(question, gap)
