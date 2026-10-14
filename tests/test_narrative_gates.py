from filingcopilot.narrative_gates import gate_narrative_claim


def test_narrative_gate_blocks_numeric_analysis_and_direction_conflict(tools):
    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    growth = tools.calculate("growth", current["id"], prior["id"])["evidence"]
    evidence = [tools.ledger[growth["id"]]]
    numeric = gate_narrative_claim(
        {"kind": "analysis", "statement": "营收增长 10%"}, evidence, tools.domain
    )
    assert not numeric.allowed and any("typed" in reason for reason in numeric.reasons)
    conflict = gate_narrative_claim(
        {"kind": "narrative", "statement": "营收下降"},
        [{"kind": "passage", "text": "营业收入增长"}],
        tools.domain,
    )
    assert not conflict.allowed and any("contradict" in reason for reason in conflict.reasons)


def test_narrative_gate_allows_a_qualified_open_question(tools):
    passage = tools.search_passages("risk", "AAPL", 1, lexical_only=True)["hits"][0]["passage"]
    evidence = [tools.ledger[passage["id"]]]
    result = gate_narrative_claim(
        {"kind": "narrative", "statement": "后续需要观察风险是否扩大"},
        evidence,
        tools.domain,
    )
    assert result.allowed
