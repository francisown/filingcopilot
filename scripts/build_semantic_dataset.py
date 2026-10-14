"""Build the versioned semantic question set from auditable templates."""

from __future__ import annotations

import json
from pathlib import Path

COMPANIES = [("AAPL", "苹果/Apple"), ("AMZN", "亚马逊/Amazon"), ("MSFT", "微软/Microsoft"), ("NVDA", "英伟达/NVIDIA"), ("TSM", "台积电/TSMC")]
YEARS = [2022, 2023, 2024, 2025]
TEMPLATES = {
    "numeric": [
        "{name} FY{year} 营业收入和净利润是多少？",
        "What were {name}'s FY{year} revenue and net income?",
        "{name} FY{year} 经营活动现金流是多少，来源是什么？",
        "What was {name}'s FY{year} operating income margin?",
    ],
    "causality": [
        "{name} FY{year} 收入变化的披露原因是什么？",
        "Why did {name}'s revenue change in FY{year}, based on filed evidence?",
        "{name} 的 FY{year} 利润率变化由哪些已披露因素解释？",
        "Which disclosed drivers explain {name}'s FY{year} growth?",
    ],
    "negation": [
        "{name} FY{year} 是否披露没有重大供应链影响？请核对原文。",
        "Did {name} state that there was no material supply-chain impact in FY{year}?",
        "{name} FY{year} 是否明确否认需求下降？",
        "Does the FY{year} filing deny a demand decline for {name}?",
    ],
    "risk": [
        "{name} FY{year} 的监管、需求和集中度风险有哪些？",
        "What regulatory, demand, and concentration risks did {name} disclose for FY{year}?",
        "{name} FY{year} 年报有哪些需要持续跟踪的风险？",
        "Which risks should an analyst track in {name}'s FY{year} filing?",
    ],
    "cross_period": [
        "比较 {name} FY{year} 与上一财年的收入增长，期间是否一致？",
        "Compare {name}'s FY{year} revenue with the prior fiscal year using the same period basis.",
        "{name} FY{year} 经营现金流同比是否可计算？",
        "Can {name}'s FY{year} operating cash flow growth be calculated without mixing periods?",
    ],
    "insufficient_evidence": [
        "仅凭年报能否判断 {name} 下季度股价？请说明缺口。",
        "Can the annual filing alone predict {name}'s next-quarter stock price? State the evidence gap.",
        "{name} 是否一定会跑赢同行？如果证据不足应如何回答？",
        "Is it proven that {name} will outperform peers, or should the answer abstain?",
    ],
    "market_outage": [
        "如果行情源不可用，{name} 的估值问题应如何返回？",
        "How should the system respond when the market source for {name} is unavailable?",
        "{name} 的年报仍可用但实时价格失败时，哪些结论可以保留？",
        "If {name}'s filing is available but its quote fails, which conclusions remain valid?",
    ],
}


def build() -> dict:
    items = []
    for category, templates in TEMPLATES.items():
        index = 0
        while index < 20:
            ticker, name = COMPANIES[(index + len(category)) % len(COMPANIES)]
            year = YEARS[(index + len(category)) % len(YEARS)]
            template = templates[index % len(templates)]
            question = template.format(name=name.split("/")[0] if "中文" in template else name.split("/")[-1], year=year)
            language = "zh" if any(ch in question for ch in "的是否能否如果凭仅比较哪些") else "en"
            if language == "zh" and "/" in name:
                question = question.replace(name.split("/")[-1], name.split("/")[0])
            expected = {
                "numeric": "answer_with_typed_fact_or_calculation",
                "causality": "cite_narrative_evidence_and_avoid_unproven_cause",
                "negation": "preserve_negation_and_require_exact_support",
                "risk": "cite_risk_evidence_and_keep_scope",
                "cross_period": "same_period_comparison_or_explicit_gap",
                "insufficient_evidence": "reasonable_refusal_with_actionable_gap",
                "market_outage": "partial_answer_with_source_failure_and_next_step",
            }[category]
            items.append(
                {
                    "id": f"semantic-{category}-{index + 1:03d}",
                    "category": category,
                    "language": language,
                    "ticker": ticker,
                    "fiscal_year": year,
                    "question": question,
                    "expected_behavior": expected,
                    "minimum_answer_chars": 40,
                }
            )
            index += 1
    return {
        "schema": "filingcopilot.semantic-gold/v1",
        "version": "2026-10-11",
        "categories": list(TEMPLATES),
        "count": len(items),
        "notes": "Versioned question set for coverage and evidence-boundary evaluation; it is not a generated-answer accuracy claim.",
        "items": items,
    }


if __name__ == "__main__":
    path = Path("evaluation/semantic_questions_v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
