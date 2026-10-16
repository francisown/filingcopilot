import json
from datetime import date
from decimal import Decimal

import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.audit import audit_package, create_package, digest
from filingcopilot.fundamentals import STOCK_GAPS, is_stock_question, stock_gaps
from filingcopilot.models import Claim, Fact, Filing, ResearchRequest, Submission
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.mark.parametrize(
    "question,expected",
    [
        ("亚马逊股票怎么样？", True),
        ("这只股票值得买吗？", True),
        ("AMZN 的估值贵吗？", True),
        ("How good is AMZN stock?", True),
        ("苹果 FY2025 营收是多少？", False),
        ("股票价格是多少？", False),
    ],
)
def test_stock_assessment_intent(question, expected):
    assert is_stock_question(question) is expected


def test_overview_computes_growth_from_observed_facts_and_reports_gaps(tools):
    result = tools.get_company_overview("AAPL")
    growth = next(e for e in result["evidence"] if e.get("operation") == "growth")
    assert growth["value"] == "100"
    assert growth["input_ids"] == ["rev25", "rev24"]
    assert growth["id"] in tools.ledger
    assert any("operating_income" in g for g in result["missing"])
    assert result["coverage_gaps"] and "行情" in result["coverage_gaps"][0]


def test_overview_has_no_future_or_missing_year_facts(tools):
    tools.as_of = date(2025, 10, 30)
    assert not tools.get_company_overview("AAPL")["found"]
    assert not tools.ledger
    tools.as_of = date(2026, 10, 8)
    assert not tools.get_company_overview("AAPL", 2030)["found"]


def positive_growth(tools):
    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    prior = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    return tools.calculate("growth", current["id"], prior["id"])["evidence"]


def approve(question, claims):
    return {
        "verdicts": [
            {
                "index": c["index"],
                "supported": True,
                "reason": "Observed revenue growth supports the qualified interpretation.",
            }
            for c in claims
        ]
    }


def test_analysis_can_interpret_verified_calculation_but_is_labelled_inference(tools):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="营收增长支持经营表现改善，当前价格的吸引力仍需估值证据。",
    )
    reviewed = []

    def reviewer(question, claims):
        reviewed.extend(claims)
        return approve(question, claims)

    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果股票怎么样？", reviewer, "AAPL"
    )
    assert not errors and accepted[0]["verification"] == "inference_review"
    assert reviewed[0]["kind"] == "analysis"
    assert "100.00%" in reviewed[0]["evidence"][0]["text"]
    assert reviewed[0]["evidence"][0]["filings"]


def test_chinese_adjacent_fiscal_year_is_not_a_financial_number(tools):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="苹果FY2025营收增长支持年度经营改善，当前估值仍需核验。",
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors


@pytest.mark.parametrize("form", ["10-Q", "10-K", "10-K/A"])
def test_source_form_name_in_qualitative_analysis_is_not_a_financial_amount(tools, form):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement=f"结合公司{form}中观察到的营收增长，年度经营表现改善，估值仍需核验。",
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors
    claim.statement += "预计上涨10美元。"
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize("text", ["利润率为 20%。", "合理市盈率是 2025 倍。", "未来上涨一百美元。"])
def test_analysis_cannot_smuggle_model_written_financial_numbers(tools, text):
    growth = positive_growth(tools)
    claim = Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and any("qualitative" in error for error in errors)


@pytest.mark.parametrize(
    "text",
    [
        "营业收入同比约增两成、营业利润逾四成。",
        "营收增长百分之二十。",
        "利润率提高三个百分点。",
        "利润率为千分之五。",
        "净利率为万分之三。",
        "现金支出约三千万港元。",
        "营收达到一百欧元。",
        "每股收益为贰点伍美元。",
        "营业利润同比翻番。",
        "营收同比翻倍。",
        "收入增加一倍半。",
        "利润增长半成。",
    ],
)
def test_chinese_numeric_claims_cannot_bypass_typed_output(tools, text):
    growth = positive_growth(tools)

    def must_not_review(*args):
        raise AssertionError("Model-written financial numbers must be caught before review")

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)]),
        tools,
        "股票怎么样？",
        must_not_review,
        "AAPL",
    )
    assert not accepted and any("numeric claims need typed results" in e for e in errors)


@pytest.mark.parametrize("text", ["营收增长两成。", "营收增加百分之二十。", "利润翻番。"])
def test_narrative_cannot_bypass_numeric_output_with_chinese_words(tools, text):
    tools.search_passages("supply", "AAPL", k=1)

    def must_not_review(*args):
        raise AssertionError("Financial numbers need typed claims, even in narrative")

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="narrative", evidence_ids=["p1"], statement=text)]),
        tools,
        "经营情况怎么样？",
        must_not_review,
        "AAPL",
    )
    assert not accepted and any("typed fact/calculation claims" in e for e in errors)


@pytest.mark.parametrize(
    "text",
    [
        "年度营收增长支持经营改善，成本仍需观察。",
        "营收增长支持经营改善，单一倍数不足以判断便宜或昂贵。",
        "年度营收增长支持经营改善，单点倍数不足以判断估值合理。",
        "年度营收增长支持经营改善，不能一成不变地外推。",
        "年度营收增长支持经营改善，二者不能直接推出价格吸引力。",
    ],
)
def test_chinese_qualitative_idioms_remain_reviewable(tools, text):
    growth = positive_growth(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)]),
        tools,
        "股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


@pytest.mark.parametrize("ending", ["", "当前为一倍。", "当前为二十倍。", "当前为20倍。"])
def test_single_multiple_idiom_is_qualitative_but_added_amounts_are_not(tools, ending):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="年度营收增长支持经营改善，单一倍数不能判断便宜或昂贵。" + ending,
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果股票怎么样？", approve, "AAPL"
    )
    assert bool(accepted) is (not ending)
    assert bool(errors) is bool(ending)


@pytest.mark.parametrize("ending", ["", "达到零点五倍。", "达到一点五倍。"])
def test_point_multiple_idiom_has_no_numeric_digit_but_real_decimals_do(tools, ending):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="单点倍数不足以判断估值合理，需同业比较。" + ending,
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果股票怎么样？", approve, "AAPL"
    )
    assert bool(accepted) is (not ending)
    assert bool(errors) is bool(ending)


def test_analysis_requires_observed_company_evidence_and_review(tools):
    claim = Claim(kind="analysis", evidence_ids=["invented"], statement="公司值得关注。")
    accepted, errors = verify_submission(Submission(claims=[claim]), tools, "股票怎么样？", approve)
    assert not accepted and any("not observed" in e for e in errors)
    growth = positive_growth(tools)
    claim.evidence_ids = [growth["id"]]
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "微软股票怎么样？", approve
    )
    assert not accepted and any("company" in e for e in errors)
    accepted, errors = verify_submission(Submission(claims=[claim]), tools, "苹果股票怎么样？")
    assert not accepted and any("unavailable" in e for e in errors)


def test_unsupported_valuation_analysis_is_withheld_before_review(tools):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis", evidence_ids=[growth["id"]], statement="当前股票估值便宜，可以立即买入。"
    )

    def reject(question, claims):
        raise AssertionError("Unsupported valuation level must be rejected before model review")

    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", reject, "AAPL"
    )
    assert not accepted and "benchmarks" in errors[0]


@pytest.mark.parametrize("statement", ["基本面呈加速改善态势。", "现金流质量同步抬升。"])
def test_one_year_growth_does_not_prove_acceleration_or_quality(tools, statement):
    growth = positive_growth(tools)
    claim = Claim(kind="analysis", evidence_ids=[growth["id"]], statement=statement)
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and errors


@pytest.mark.parametrize("statement", ["营收同比亦加快。", "营业收入增长加快。", "营收增速加快。"])
def test_faster_growth_requires_comparable_growth_rates(tools, statement):
    growth = positive_growth(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=statement)]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("acceleration" in error for error in errors)


def test_faster_growth_can_use_two_improving_consecutive_annual_rates(tools):
    filing = Filing.model_validate(tools.list_filings("AAPL")["filings"][0])
    tools.corpus.add_facts(
        filing,
        [
            Fact(
                id="rev23",
                filing_id=filing.id,
                ticker="AAPL",
                metric="revenue",
                label="营业收入",
                concept="us-gaap:Test",
                value="90",
                unit="USD",
                fiscal_year=2023,
                start="2022-10-01",
                end="2023-09-27",
                source_url="https://data.sec.gov/test.json",
                locator="fixture 2023 record",
            )
        ],
    )
    current = positive_growth(tools)
    prior_fact = tools.get_financial_fact("AAPL", "revenue", 2023)["evidence"]
    previous = tools.calculate("growth", "rev24", prior_fact["id"])["evidence"]
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="analysis",
                    evidence_ids=[current["id"], previous["id"]],
                    statement="年度营收增长加快，当前估值仍需核验。",
                )
            ]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert accepted and not errors


def test_future_cash_transition_is_a_condition_not_an_observed_sign_change(tools):
    growth = positive_growth(tools)
    text = "判断当前价格需先验证未来盈利增速与自由现金流转正节奏能否承接。"
    claim = Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors


@pytest.mark.parametrize(
    "text",
    [
        "价格成立仍取决于盈利增速、自由现金流转正进度及可比同业证据。",
        "价格合理性取决于后续盈利能否维持、自由现金流何时转正。",
        "判断价格合理性，需观察自由现金流能否转正。",
        "自由现金流转正时点仍须后续披露验证。",
        "需跟踪自由现金流转正节奏。",
        "如果未来自由现金流转正，仍需核验可比估值。",
        "股价合理性仍需盈利增速、自由现金流与可比同业证据支撑。",
        "市盈率不能单独证明便宜或昂贵。",
        "年度与供应商TTM两套市盈率口径均不能单独证明便宜或昂贵，股价合理性仍需盈利增速、自由现金流与可比同业/历史倍数证据支撑。",
        "需核验自由现金流是否已经转正。",
        "需核验是否自由现金流已转正。",
        "需验证自由现金流由负转正的未来时点。",
    ],
)
def test_unobserved_valuation_conditions_are_sent_for_semantic_review(tools, text):
    growth = positive_growth(tools)
    seen = []

    def reviewer(question, claims):
        seen.extend(claims)
        return approve(question, claims)

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)]),
        tools,
        "股票怎么样？",
        reviewer,
        "AAPL",
    )
    assert accepted and not errors and len(seen) == 1


@pytest.mark.parametrize(
    "text",
    [
        "需观察自由现金流是否持续，但自由现金流已经转正。",
        "自由现金流已经转正，时点仍需观察。",
        "股价合理性取决于自由现金流已转正的事实。",
        "需跟踪未来增长；自由现金流已转正。",
        "自由现金流转正已经完成，未来仍需观察。",
        "预计明年自由现金流将转正。",
        "当前股价合理，未来自由现金流还需验证。",
        "股价合理性已由经营增长证明，仍需观察现金投入。",
        "估值便宜但自由现金流何时转正仍需观察。",
        "估值能否成立取决于自由现金流已转正的事实。",
        "需要观察其他指标，自由现金流转正了。",
        "需跟踪自由现金流转正的事实。",
        "需验证盈利增速与自由现金流转正的事实。",
        "供应商TTM市盈率口径独立，股价合理性需最新季度毛利率已经上升支撑。",
        "供应商TTM市盈率口径独立，股价合理性已有自由现金流支撑。",
    ],
)
def test_valuation_conditions_do_not_hide_asserted_facts_or_predictions(tools, text):
    growth = positive_growth(tools)
    reviewed = []

    def must_not_review(*args):
        reviewed.append(args)
        raise AssertionError("Unsupported observed claims must be blocked before review")

    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)]),
        tools,
        "股票怎么样？",
        must_not_review,
        "AAPL",
    )
    assert not accepted and errors and not reviewed


@pytest.mark.parametrize(
    "text",
    [
        "自由现金流已经转正。",
        "需先验证未来增长但自由现金流已经转正。",
        "需先验证未来增长；自由现金流已经转正。",
    ],
)
def test_future_condition_cannot_hide_an_actual_unsupported_cash_transition(tools, text):
    growth = positive_growth(tools)
    accepted, errors = verify_submission(
        Submission(claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=text)]),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("sign transition" in error for error in errors)


@pytest.mark.parametrize(
    "limit",
    [
        "一年改善不等于长期加速。",
        "单年趋势不可外推为长期加速。",
        "单年数据不可外推为增长加速。",
        "单一年度不支持长期加速或可持续判断。",
        "单季度不能证明长期加速。",
        "单季增速不证明年度已加速。",
        "单季增速不构成加速判断。",
        "单季增速不证明增长加快。",
        "单季增速不能证明增速加快。",
        "单年年度证据不足以论证增速加速。",
    ],
)
def test_explicit_acceleration_limit_does_not_withhold_supported_growth(tools, limit):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="年度营收同比增长，支持当年的经营规模改善；" + limit,
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "苹果股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors


@pytest.mark.parametrize(
    "statement",
    [
        "一年改善不等于长期加速，但营收增速已经提升并加速。",
        "单年趋势不可外推为增长加速，但营收已经加速。",
        "一年改善不支持长期加速却已经增长提速。",
        "一年改善不等于没有长期加速。",
        "单季度不能证明长期加速但营收同比亦加快。",
        "单年证据不足以论证增速加速但营收同比加快。",
    ],
)
def test_acceleration_limit_cannot_hide_another_unsupported_assertion(tools, statement):
    growth = positive_growth(tools)
    accepted, errors = verify_submission(
        Submission(
            claims=[Claim(kind="analysis", evidence_ids=[growth["id"]], statement=statement)]
        ),
        tools,
        "苹果股票怎么样？",
        approve,
        "AAPL",
    )
    assert not accepted and any("acceleration" in error for error in errors)


@pytest.mark.parametrize(
    "statement",
    [
        "毛利率较上年走高。",
        "毛利率高于上年同期。",
        "毛利率高于前一年。",
        "Gross margin percentage was higher than the prior year.",
    ],
)
def test_explicit_margin_change_needs_both_years_and_correct_direction(tools, statement):
    facts = [
        f
        for metric in ("revenue", "gross_profit")
        for year in (2024, 2025)
        for f in tools.corpus.facts("AAPL", metric, year, tools.as_of)
    ]
    previous_gross = next(f for f in facts if f.metric == "gross_profit").model_copy(
        update={
            "id": "gross24",
            "fiscal_year": 2024,
            "value": Decimal(30),
            "start": date(2023, 10, 1),
            "end": date(2024, 9, 27),
        }
    )
    tools.corpus.ingest(
        Filing.model_validate(tools.corpus.catalog(tools.as_of, "AAPL")[0]),
        [*facts, previous_gross],
        tools.corpus.passages(tools.as_of, "AAPL"),
    )
    margins = []
    for year in (2025, 2024):
        gross = tools.get_financial_fact("AAPL", "gross_profit", year)["evidence"]
        revenue = tools.get_financial_fact("AAPL", "revenue", year)["evidence"]
        margins.append(tools.calculate("ratio", gross["id"], revenue["id"])["evidence"])
    claim = Claim(kind="analysis", evidence_ids=[margins[0]["id"]], statement=statement)
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and any("both comparable" in e for e in errors)
    claim.evidence_ids = [m["id"] for m in margins]
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors
    claim.statement = "毛利率较上年回落。"
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and any("direction contradicts" in e for e in errors)


def test_qualified_quality_gap_remains_allowed(tools):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="营收增长是经营的正面信号，盈利质量是否改善尚需核验。",
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors


def test_stock_coverage_gaps_are_not_repeated_but_missing_subject_is_retained():
    gaps = stock_gaps(
        [
            "尚未接入带时间戳的股票行情与估值数据，无法核验当前买入价格是否合理。",
            "本次仅基于年度申报，尚未补齐最新季报与新闻。",
            "经营现金流不等于自由现金流；资本开支与一次性损益尚未经核验。",
            "公司债务到期分布尚未读取。",
        ]
    )
    assert gaps == [*STOCK_GAPS, "公司债务到期分布尚未读取。"]


def test_supplier_coverage_replaces_generic_missing_multiple_but_preserves_failed_source():
    gaps = stock_gaps(
        ["尚未计算 TTM 市盈率。", "公司债务到期分布尚未读取。", "TTM 自由现金流同比缺少更早窗口。"],
        has_market=True,
        has_valuation=True,
        has_supplier_valuation=True,
        valuation_failures=["供应商估值刷新失败，请重试。"],
    )
    assert not any("尚未计算 TTM" in gap for gap in gaps)
    assert "公司债务到期分布尚未读取。" in gaps
    assert "TTM 自由现金流同比缺少更早窗口。" in gaps
    assert "供应商估值刷新失败，请重试。" in gaps


class StockGateway:
    def __init__(self):
        self.saw_overview = False

    def complete(self, messages, tools, review=False):
        if review:
            claims = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {"content": json.dumps(approve("", claims))},
                "usage": {"total_tokens": 10},
            }
        request = json.loads(messages[1]["content"])
        overview = request["annual_overview"]
        self.saw_overview = bool(overview["evidence"])
        growth = next(e for e in overview["evidence"] if e.get("operation") == "growth")
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
                                            "evidence_ids": [growth["id"]],
                                            "statement": "营收增长支持经营表现改善，值得继续研究；买入价格的吸引力仍需估值证据。",
                                        },
                                        {
                                            "kind": "calculation",
                                            "evidence_ids": [growth["id"]],
                                            "statement": "",
                                        },
                                    ],
                                    "gaps": [],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            "usage": {"total_tokens": 10},
        }


def test_stock_agent_prefetches_fundamentals_and_exports_auditable_assessment(tools):
    gateway = StockGateway()
    settings = Settings(home=tools.corpus.home, embedding_mode="off", min_model_interval=0)
    answer = ResearchAgent(settings, gateway).run(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert gateway.saw_overview
    assert answer.status == "partial" and answer.research_type == "stock"
    assert answer.answer[0]["kind"] == "analysis"
    assert answer.usage["model_calls"] == 2
    assert answer.trace[0]["tool"] == "get_company_overview"
    assert any("估值" in gap for gap in answer.gaps)
    package = create_package(answer, tools.domain, "AAPL")
    audit = audit_package(package, corpus=tools.corpus, trusted_domain=tools.domain)
    assert audit["status"] == "consistent" and audit["source_validation"] == "matched"
    assert audit["numeric_claims_recomputed"] == 1
    assert audit["narrative_claims_not_rechecked"] == 1
    assert any(
        c["code"] == "semantic_entailment" and c["status"] == "not_checked" for c in audit["checks"]
    )
    package["research"]["answer"][0]["verification"] = "deterministic"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


@pytest.mark.parametrize("batch_size,expected_batches,expected_calls", [(2, [2, 1], 3), (8, [3], 2)])
def test_review_batches_and_deduplicates_shared_evidence(tools, batch_size, expected_batches, expected_calls):
    class BatchGateway(StockGateway):
        def __init__(self):
            super().__init__()
            self.reviews = []

        def complete(self, messages, schemas, review=False):
            if review:
                self.reviews.append(json.loads(messages[1]["content"]))
                return super().complete(messages, schemas, review=True)
            result = super().complete(messages, schemas)
            call = result["message"]["tool_calls"][0]["function"]
            submission = json.loads(call["arguments"])
            original = submission["claims"][0]
            submission["claims"] = [
                {**original, "statement": text}
                for text in (
                    "营收增长是经营的正面信号。",
                    "营收改善仍不足确定当前估值吸引力。",
                    "后续需观察增长能否延续，以更新经营判断。",
                )
            ]
            call["arguments"] = json.dumps(submission, ensure_ascii=False)
            return result

    gateway = BatchGateway()
    answer = ResearchAgent(
        Settings(home=tools.corpus.home, min_model_interval=0, review_batch_size=batch_size), gateway
    ).run(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert len(answer.answer) == 3
    assert [len(r["claims"]) for r in gateway.reviews] == expected_batches
    assert all(len(r["evidence"]) == 1 for r in gateway.reviews)
    assert answer.usage["model_calls"] == expected_calls
    assert all(t["verdicts"] for t in answer.trace if t["stage"] == "semantic_review")


@pytest.mark.parametrize("failure", ["malformed_json", "wrong_indexes", "connection_error"])
def test_failed_review_batch_preserves_other_approved_claims_and_withholds_unreviewed(
    tools, failure
):
    class FailedBatchGateway(StockGateway):
        review_count = 0

        def complete(self, messages, schemas, review=False):
            if review:
                self.review_count += 1
                if self.review_count == 2:
                    if failure == "connection_error":
                        raise RuntimeError("Reviewer connection unavailable")
                    content = "{" if failure == "malformed_json" else '{"verdicts":[]}'
                    return {"message": {"content": content}, "usage": {"total_tokens": 10}}
                return super().complete(messages, schemas, review=True)
            result = super().complete(messages, schemas)
            call = result["message"]["tool_calls"][0]["function"]
            submission = json.loads(call["arguments"])
            original = submission["claims"][0]
            submission["claims"] = [
                {**original, "statement": text}
                for text in (
                    "营收增长是经营的正面信号。",
                    "营收增长不足以确定当前估值吸引力。",
                    "后续需观察增长能否延续。",
                )
            ]
            call["arguments"] = json.dumps(submission, ensure_ascii=False)
            return result

    answer = ResearchAgent(
        Settings(home=tools.corpus.home, max_rounds=1), FailedBatchGateway()
    ).run(ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools)
    assert answer.status == "partial"
    assert [c["index"] for c in answer.answer] == [0, 1]
    assert any(t["stage"] == "semantic_review_error" for t in answer.trace)
    assert any("未通过" in gap for gap in answer.gaps)
    assert audit_package(create_package(answer, tools.domain, "AAPL"))["status"] == "consistent"


def test_truncated_generation_retries_within_round_budget(tools):
    class TruncatedGateway(StockGateway):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete(self, messages, schemas, review=False):
            if not review:
                self.calls += 1
                if self.calls == 1:
                    return {
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "length",
                    }
                assert any("truncated" in m.get("content", "") for m in messages[-3:])
            return super().complete(messages, schemas, review)

    gateway = TruncatedGateway()
    answer = ResearchAgent(Settings(home=tools.corpus.home, min_model_interval=0), gateway).run(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert gateway.calls == 2 and answer.answer[0]["kind"] == "analysis"


def test_repeated_truncation_stops_at_round_limit(tools):
    class TruncatedGateway:
        calls = 0

        def complete(self, *args, **kwargs):
            self.calls += 1
            return {"message": {"role": "assistant", "content": ""}, "finish_reason": "length"}

    gateway = TruncatedGateway()
    answer = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), gateway).run(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert gateway.calls == 2 and answer.status == "abstained"


def verification_state(tools, claims):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    return {
        "submission": Submission(claims=claims),
        "tools": tools,
        "question": "苹果股票怎么样？",
        "focus_ticker": "AAPL",
        "rounds": 1,
        "repairs": 0,
        "messages": [],
        "verified": [],
        "errors": [],
        "trace": [],
        "usage": dict.fromkeys(
            ("model_calls", "prompt_tokens", "completion_tokens", "total_tokens"), 0
        ),
        "action": "",
    }


class UnavailableRepairReviewer:
    calls = 0

    def complete(self, messages, schemas, review=False):
        assert review
        self.calls += 1
        if self.calls > 1:
            return {"message": {"content": ""}, "finish_reason": "length"}
        return {
            "message": {
                "content": json.dumps(approve("", json.loads(messages[1]["content"])["claims"]))
            }
        }


def test_repair_reuses_approval_only_for_unchanged_claim_and_full_evidence(tools):
    reviewer = UnavailableRepairReviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), reviewer)
    claim = Claim(
        kind="analysis", evidence_ids=["rev25"], statement="已披露年度营收，为经营规模提供依据。"
    )
    state = verification_state(tools, [claim, Claim(kind="fact", evidence_ids=["missing"])])
    first = agent.verify(state)
    assert first["verified"][0]["kind"] == "analysis"
    second = agent.verify({**first, "rounds": 2})
    assert second["verified"][0]["text"] == claim.statement
    assert reviewer.calls == 1
    assert any(t["stage"] == "semantic_review_cached" for t in second["trace"])


def test_repair_feedback_preserves_exact_approved_claims_and_question(tools):
    reviewer = UnavailableRepairReviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), reviewer)
    claim = Claim(
        kind="analysis", evidence_ids=["rev25"], statement="已披露年度营收，为经营规模提供依据。"
    )
    state = verification_state(tools, [claim, Claim(kind="fact", evidence_ids=["missing"])])
    first = agent.verify(state)
    feedback = json.loads(first["messages"][-1]["content"])
    assert feedback["question"] == state["question"]
    assert feedback["approved_claims"] == [claim.model_dump(mode="json")]
    assert feedback["rejected_claims"][0]["claim"]["evidence_ids"] == ["missing"]
    assert feedback["findings"]


def test_graph_repair_can_copy_approved_claim_without_second_review(tools):
    class Gateway:
        reviews = 0
        drafts = 0

        def complete(self, messages, schemas, review=False):
            if review:
                self.reviews += 1
                return {
                    "message": {
                        "content": json.dumps(
                            approve("", json.loads(messages[1]["content"])["claims"])
                        )
                    }
                }
            self.drafts += 1
            if self.drafts == 1:
                context = json.loads(messages[1]["content"])
                growth = next(
                    e
                    for e in context["annual_overview"]["evidence"]
                    if e.get("operation") == "growth"
                )
                claims = [
                    {
                        "kind": "analysis",
                        "statement": "年度营收同比增加；持续性仍需后续披露验证。",
                        "evidence_ids": [growth["id"]],
                    },
                    {"kind": "fact", "evidence_ids": ["missing"]},
                ]
            else:
                feedback = next(
                    json.loads(m["content"])
                    for m in reversed(messages)
                    if m.get("content", "").startswith('{"type":"verification_feedback"')
                )
                assert feedback["question"] == "苹果股票怎么样？"
                assert len(feedback["rejected_claims"]) == 1
                claims = feedback["approved_claims"]
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
                                    {"claims": claims, "gaps": ["已移除缺少来源的数字。"]}
                                ),
                            },
                        }
                    ],
                }
            }

    gateway = Gateway()
    answer = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=3), gateway).run(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert gateway.drafts == 2 and gateway.reviews == 1
    assert answer.answer[0]["text"] == "年度营收同比增加；持续性仍需后续披露验证。"
    assert any(t["stage"] == "semantic_review_cached" for t in answer.trace)


@pytest.mark.parametrize("change", ["statement", "evidence", "question"])
def test_changed_review_input_cannot_inherit_approval(tools, change):
    reviewer = UnavailableRepairReviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), reviewer)
    claim = Claim(
        kind="analysis", evidence_ids=["rev25"], statement="已披露年度营收，为经营规模提供依据。"
    )
    first = agent.verify(verification_state(tools, [claim]))
    changed_text = "已披露年度营收，后续仍需跟踪经营表现。"
    next_state = {**first, "rounds": 2}
    if change == "statement":
        next_state["submission"] = Submission(
            claims=[claim.model_copy(update={"statement": changed_text})]
        )
    elif change == "evidence":
        tools.ledger["rev25"] = {**tools.ledger["rev25"], "value": "201"}
    else:
        next_state["question"] = "苹果股票经营表现怎么样？"
    final = agent.verify(next_state)
    assert reviewer.calls == 2
    assert not any(c["text"] == changed_text for c in final["verified"])
    if change != "statement":
        assert not any(c["kind"] == "analysis" for c in final["verified"])


def test_unavailable_rewritten_draft_keeps_previous_coherent_verified_partial(tools):
    reviewer = UnavailableRepairReviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), reviewer)
    original = Claim(
        kind="analysis", evidence_ids=["rev25"], statement="已披露年度营收，为经营规模提供依据。"
    )
    first = agent.verify(
        verification_state(tools, [original, Claim(kind="fact", evidence_ids=["missing"])])
    )
    second = agent.verify(
        {
            **first,
            "rounds": 2,
            "submission": Submission(
                claims=[
                    Claim(
                        kind="analysis",
                        evidence_ids=["rev25"],
                        statement="已披露年度营收，后续仍需跟踪经营表现。",
                    ),
                    Claim(kind="fact", evidence_ids=["rev25"]),
                ]
            ),
        }
    )
    assert [c["text"] for c in second["verified"]] == [original.statement]
    assert second["errors"]
    assert any(t["stage"] == "retained_verified_partial" for t in second["trace"])


def test_explicit_rejection_of_reworded_claim_invalidates_old_shared_evidence_approval(tools):
    class Reviewer:
        calls = 0

        def complete(self, messages, schemas, review=False):
            self.calls += 1
            claims = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": c["index"],
                                    "supported": self.calls == 1,
                                    "reason": "Initial mistaken approval"
                                    if self.calls == 1
                                    else "Disclosed revenue does not establish debt-funded cash investment",
                                }
                                for c in claims
                            ]
                        }
                    )
                }
            }

    growth = positive_growth(tools)
    good = Claim(
        kind="analysis", evidence_ids=[growth["id"]], statement="年度营收同比增长；持续性仍需核验。"
    )
    original = Claim(
        kind="analysis",
        evidence_ids=["rev25"],
        statement="年度营收已披露，现金投入部分依赖新债融资。",
    )
    reviewer = Reviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), reviewer)
    first = agent.verify(
        verification_state(tools, [good, original, Claim(kind="fact", evidence_ids=["missing"])])
    )
    assert len(first["verified"]) == 2
    changed = original.model_copy(update={"statement": "营收已经披露，新增债务部分支持现金投入。"})
    second = agent.verify(
        {
            **first,
            "rounds": 2,
            "submission": Submission(
                claims=[good, changed, Claim(kind="fact", evidence_ids=["missing"])]
            ),
        }
    )
    assert reviewer.calls == 2
    assert [c["text"] for c in second["verified"]] == [good.statement]


def test_conflicted_basis_requires_fresh_review_even_for_original_wording(tools):
    class Reviewer:
        calls = 0

        def complete(self, messages, schemas, review=False):
            self.calls += 1
            rows = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": r["index"],
                                    "supported": self.calls != 2,
                                    "reason": "controlled changed verdict",
                                }
                                for r in rows
                            ]
                        }
                    )
                }
            }

    reviewer = Reviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=3), reviewer)
    claim = Claim(
        kind="analysis", evidence_ids=["rev25"], statement="已披露年度营收，为经营规模提供依据。"
    )
    first = agent.verify(
        verification_state(tools, [claim, Claim(kind="fact", evidence_ids=["missing"])])
    )
    changed = claim.model_copy(update={"statement": "年度营收已经公开，为经营规模提供依据。"})
    second = agent.verify({**first, "rounds": 2, "submission": Submission(claims=[changed])})
    assert not second["verified"]
    third = agent.verify({**second, "rounds": 3, "submission": Submission(claims=[claim])})
    assert reviewer.calls == 3 and third["verified"][0]["text"] == claim.statement


def test_incomplete_repair_retains_richer_coherent_draft_without_merging(tools):
    class Reviewer:
        def complete(self, messages, schemas, review=False):
            assert review
            return {
                "message": {
                    "content": json.dumps(approve("", json.loads(messages[1]["content"])["claims"]))
                }
            }

    original = [
        Claim(
            kind="analysis",
            evidence_ids=["rev25"],
            statement="已披露年度营收，为经营规模提供依据。",
        ),
        Claim(kind="analysis", evidence_ids=["rev25"], statement="估值吸引力仍需同业与历史比较。"),
    ]
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), Reviewer())
    first = agent.verify(
        verification_state(tools, [*original, Claim(kind="fact", evidence_ids=["missing"])])
    )
    rewritten = original[1].model_copy(update={"statement": "当前价格的吸引力仍需估值比较。"})
    second = agent.verify(
        {
            **first,
            "rounds": 2,
            "submission": Submission(
                claims=[rewritten, Claim(kind="fact", evidence_ids=["missing"])]
            ),
        }
    )
    assert [c["text"] for c in second["verified"]] == [c.statement for c in original]
    assert not any(c["text"] == rewritten.statement for c in second["verified"])
    assert second["errors"]


@pytest.mark.parametrize("change", ["complete", "question", "evidence"])
def test_richer_draft_cannot_override_complete_repair_or_changed_inputs(tools, change):
    reviewer = UnavailableRepairReviewer()
    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=2), reviewer)
    original = [
        Claim(
            kind="analysis",
            evidence_ids=["rev25"],
            statement="已披露年度营收，为经营规模提供依据。",
        ),
        Claim(kind="analysis", evidence_ids=["rev25"], statement="估值吸引力仍需同业与历史比较。"),
    ]
    first = agent.verify(
        verification_state(tools, [*original, Claim(kind="fact", evidence_ids=["missing"])])
    )
    next_state = {**first, "rounds": 2, "submission": Submission(claims=[original[0]])}
    if change == "question":
        next_state["question"] = "苹果股票经营表现怎么样？"
    elif change == "evidence":
        tools.ledger["rev25"] = {**tools.ledger["rev25"], "value": "201"}
    final = agent.verify(next_state)
    expected = [original[0].statement] if change == "complete" else []
    assert [c["text"] for c in final["verified"]] == expected


@pytest.mark.parametrize("period", ["2026Q1", "FY2026Q4", "Q2", "2026年第1季度"])
def test_qualitative_analysis_can_name_quarter_periods(tools, period):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    claim = Claim(
        kind="analysis",
        evidence_ids=["rev25"],
        statement=f"年度营收已披露，{period}的经营更新尚需季度证据。",
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert accepted and not errors


@pytest.mark.parametrize("number", ["2026Q1倍", "2026Q5", "Q2美元", "250USD"])
def test_period_exception_cannot_disguise_financial_numbers(tools, number):
    tools.get_financial_fact("AAPL", "revenue", 2025)
    claim = Claim(
        kind="analysis", evidence_ids=["rev25"], statement=f"年度营收支持经营规模，{number}。"
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and errors


@pytest.mark.parametrize("name,expected", [("submit_review", True), ("submit_answer", False)])
def test_reviewer_accepts_only_its_structured_verdict_tool(tools, name, expected):
    class StructuredReviewer:
        def complete(self, messages, schemas, review=False):
            assert review and schemas[0]["function"]["name"] == "submit_review"
            claims = json.loads(messages[1]["content"])["claims"]
            return {
                "message": {
                    "content": json.dumps(approve("", claims)),
                    "tool_calls": [
                        {
                            "id": "review",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(approve("", claims)),
                            },
                        }
                    ],
                }
            }

    agent = ResearchAgent(Settings(home=tools.corpus.home, max_rounds=1), StructuredReviewer())
    state = verification_state(
        tools,
        [
            Claim(
                kind="analysis",
                evidence_ids=["rev25"],
                statement="已披露年度营收，为经营规模提供依据。",
            )
        ],
    )
    final = agent.verify(state)
    assert bool(final["verified"]) is expected


def test_aggregate_growth_does_not_support_embedded_high_margin_premise(tools):
    growth = positive_growth(tools)
    claim = Claim(
        kind="analysis",
        evidence_ids=[growth["id"]],
        statement="营收增长是正面信号，能否支撑价格仍需看高毛利业务后续增长。",
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "股票怎么样？", approve, "AAPL"
    )
    assert not accepted and any("margin" in e for e in errors)
