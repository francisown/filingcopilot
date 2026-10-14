import hashlib
from datetime import date
from decimal import Decimal

import httpx
import pytest

from filingcopilot.audit import audit_package, digest
from filingcopilot.cashflow import cashflow_overview
from filingcopilot.domain import Company
from filingcopilot.ingestion import extract_cash_facts, extract_facts
from filingcopilot.models import Claim, Filing, ResearchRequest, Submission
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def cash_fixture(tmp_path, ticker="AAPL", form="10-K", quarter_only=False, prior_capex=50):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    filing = Filing(
        id=ticker + ":cash",
        ticker=ticker,
        company="Cash fixture",
        cik="0001018724" if ticker == "AMZN" else "0000320193",
        accession="cash",
        form=form,
        filed="2026-07-31",
        period_end="2026-06-30",
        source_url="https://www.sec.gov/Archives/cash.htm",
        content_sha256="a" * 64,
    )
    periods = [
        ("2025-07-01", "2026-06-30", 100, 120),
        ("2024-07-01", "2025-06-30", 90, prior_capex),
    ]
    if form == "10-Q":
        periods = [("2026-01-01", "2026-06-30", 100, 120), ("2025-01-01", "2025-06-30", 90, 50)]
        if quarter_only:
            periods = [("2026-04-01", "2026-06-30", 100, 120), ("2025-04-01", "2025-06-30", 90, 50)]
    data = {
        "facts": {
            "us-gaap": {
                concept: {
                    "units": {
                        "USD": [
                            {"accn": "cash", "form": form, "start": s, "end": e, "val": values[i]}
                            for s, e, *values in periods
                        ]
                    }
                }
                for i, concept in enumerate(
                    [
                        "NetCashProvidedByUsedInOperatingActivities",
                        "PaymentsToAcquirePropertyPlantAndEquipment",
                    ]
                )
            }
        }
    }
    service.corpus.ingest(filing, extract_facts(data, filing), [])
    if ticker == "AMZN":
        service.corpus.register_company(ticker, Company(cik=filing.cik, name="Amazon fixture"))
    service.discovery.prepare_quarterly = lambda *_: {"found": form == "10-Q", "warning": None}
    return service, filing


def test_cashflow_negative_result_and_bound_inputs(tmp_path):
    service, _ = cash_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    result = cashflow_overview(tools, "AAPL", period="annual")
    assert result["found"]
    current = tools.ledger[result["current_id"]]
    assert Decimal(current["value"]) == -20
    assert current["input_ids"] == [e["id"] for e in current["inputs"]]
    assert "公司" in result["definition_note"] and "租赁" in result["definition_note"]
    change = tools.ledger[result["change_id"]]
    assert Decimal(change["value"]) == -60
    assert result["growth_id"] is not None
    assert Decimal(tools.ledger[result["growth_id"]]["value"]) == -150


def test_cashflow_cannot_mix_source_periods_or_treat_missing_as_zero(tmp_path):
    service, filing = cash_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    ocf = tools.get_financial_fact("AAPL", "operating_cash_flow", 2026)["evidence"]
    prior = tools.get_financial_fact("AAPL", "cash_capex", 2025)["evidence"]
    with pytest.raises(ValueError, match="exact"):
        tools.calculate("free_cash_flow", ocf["id"], prior["id"])
    with service.corpus.connect() as db:
        db.execute("DELETE FROM facts WHERE metric='cash_capex'")
    assert not cashflow_overview(service.tools(date(2026, 10, 8)), "AAPL")["found"]


def test_single_quarter_cashflow_fallback_is_explicit_ytd(tmp_path):
    service, _ = cash_fixture(tmp_path, form="10-Q")
    result = cashflow_overview(service.tools(date(2026, 10, 8)), "AAPL", period="quarter")
    assert result["found"] and result["period_scope"] == "ytd"
    assert any("单季度" in gap and "累计" in gap for gap in result["warnings"])


def test_cashflow_query_verification_and_export(tmp_path):
    service, _ = cash_fixture(tmp_path)
    plan = service.research_plan(
        ResearchRequest(language="zh", question="最新年度自由现金流和同比是多少？", ticker="AAPL")
    )
    assert plan["ready"] and not plan["uses_model"]
    answer = service.research(
        ResearchRequest(language="zh", question="最新年度自由现金流和同比是多少？", ticker="AAPL")
    )
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert any("自由现金流" in c["text"] and "-20" in c["text"] for c in answer.answer)
    package = service.export_package(answer.id)
    assert audit_package(package)["status"] == "consistent"
    tools = service.tools(date(2026, 10, 8))
    overview = tools.get_cashflow("AAPL", period="annual")
    claims, errors = verify_submission(
        Submission(claims=[Claim(kind="calculation", evidence_ids=[overview["current_id"]])]),
        tools,
        "自由现金流是多少？",
        focus_ticker="AAPL",
    )
    assert claims and not errors


@pytest.mark.parametrize("question", ["查询TTM市盈率"])
def test_provider_ttm_valuation_does_not_use_cashflow_or_annual_formula(tmp_path, question):
    service, _ = cash_fixture(tmp_path)
    plan = service.research_plan(ResearchRequest(language="zh", question=question, ticker="AAPL"))
    assert plan["ready"] and plan["kind"] == "provider_valuation" and not plan["uses_model"]
    assert "未逐季重建 SEC 稀释股数" in plan["message"]


def test_cashflow_abbreviation_next_to_chinese_uses_cashflow_tools(tmp_path):
    service, _ = cash_fixture(tmp_path)
    plan = service.research_plan(ResearchRequest(language="zh", question="FCF同比怎么样？", ticker="AAPL"))
    assert plan["ready"] and plan["kind"] == "cashflow" and not plan["uses_model"]


def test_nested_cashflow_growth_does_not_crash_acceleration_guard(tmp_path):
    service, _ = cash_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    cash = tools.get_cashflow("AAPL", period="annual")
    claim = Claim(
        kind="analysis", statement="自由现金流增长加速。", evidence_ids=[cash["growth_id"]]
    )
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "自由现金流怎么样？", focus_ticker="AAPL"
    )
    assert not accepted and any("two consecutive years" in error for error in errors)


def test_bound_source_date_in_analysis_does_not_allow_financial_numbers(tmp_path):
    service, _ = cash_fixture(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    cash = tools.get_cashflow("AAPL", period="annual")
    claim = Claim(
        kind="analysis",
        statement="截至2026年6月30日的年度自由现金流为负，投资支出占用经营现金。",
        evidence_ids=[cash["current_id"]],
    )

    def reviewer(_, claims):
        return {
            "verdicts": [
                {
                    "index": c["index"],
                    "supported": True,
                    "reason": "synthetic approval for syntax test",
                }
                for c in claims
            ]
        }

    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "自由现金流怎么样？", reviewer, "AAPL"
    )
    assert accepted and not errors
    claim.statement += "剩余现金为10美元。"
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "自由现金流怎么样？", reviewer, "AAPL"
    )
    assert not accepted and any("qualitative" in e for e in errors)
    claim.statement = "截至2027年6月30日的年度自由现金流为负。"
    accepted, errors = verify_submission(
        Submission(claims=[claim]), tools, "自由现金流怎么样？", reviewer, "AAPL"
    )
    assert not accepted and errors


def test_quarter_capex_query_enriches_preupgrade_facts_before_answering(tmp_path):
    service, filing = cash_fixture(tmp_path, form="10-Q", quarter_only=True)
    capex = service.corpus.facts(
        "AAPL", "cash_capex", 2026, date(2026, 10, 8), forms=("10-Q", "10-Q/A")
    )
    with service.corpus.connect() as db:
        db.execute("DELETE FROM facts WHERE metric='cash_capex'")
    tools = service.tools(date(2026, 10, 8))
    loaded = []

    def enrich(ticker):
        loaded.append(ticker)
        service.corpus.add_facts(filing, capex)

    tools.fact_loader = enrich
    result = tools.get_quarterly_fact("AAPL", "cash_capex", "2026-06-30")
    assert result["found"] and loaded == ["AAPL"]
    assert result["evidence"]["start"] == "2026-04-01"
    assert result["evidence"]["filing_id"] == filing.id


def inline_html(
    concept="us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    segment="",
    value="1,200",
    unit="iso4217:USD",
    sign="",
):
    return f'''<html><xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000320193</xbrli:identifier>{segment}</xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="u1"><xbrli:measure>{unit}</xbrli:measure></xbrli:unit><table><tr><td>Purchases of property and equipment</td><td><ix:nonFraction name="{concept}" id="f1" contextRef="c1" unitRef="u1" format="ixt:num-dot-decimal" scale="6" {sign}>{value}</ix:nonFraction></td></tr></table></html>'''.encode()


def test_inline_fact_identity_unit_dimensions_and_scale(tmp_path):
    _, filing = cash_fixture(tmp_path)
    facts = extract_cash_facts(inline_html(), filing)
    assert len(facts) == 1 and facts[0].value == Decimal(1200000000)
    assert facts[0].source_url == filing.source_url and "f1" in facts[0].locator
    assert not extract_cash_facts(
        inline_html(
            segment="<xbrli:segment><xbrldi:explicitMember>Segment</xbrldi:explicitMember></xbrli:segment>"
        ),
        filing,
    )
    assert not extract_cash_facts(inline_html(unit="iso4217:EUR"), filing)
    assert not extract_cash_facts(inline_html(value="N/A"), filing)
    assert not extract_cash_facts(inline_html(sign='sign="-"'), filing)
    assert not extract_cash_facts(
        inline_html(concept="us-gaap:PaymentsToAcquireProductiveAssets"), filing
    )


def test_inline_source_recomputed_and_tampering_rejected(tmp_path):
    service, filing = cash_fixture(tmp_path)
    html = inline_html(value="120")
    filing = filing.model_copy(update={"content_sha256": hashlib.sha256(html).hexdigest()})
    facts = extract_cash_facts(html, filing)
    # Use real inline provenance rather than the synthetic companyfacts record.
    service.corpus.ingest(filing, facts, [])
    cache = tmp_path / "sec-cache"
    cache.mkdir(exist_ok=True)
    raw = cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")
    raw.write_bytes(html)
    result = service.research(
        ResearchRequest(language="zh", question="AAPL 2026 现金资本开支是多少？", ticker="AAPL", mode="reference")
    )
    package = service.export_package(result.id)
    assert audit_package(package, source_cache=cache)["status"] == "consistent"
    raw.write_bytes(html.replace(b"120</ix:", b"121</ix:"))
    assert audit_package(package, source_cache=cache)["status"] == "invalid"


def test_negative_prior_cashflow_has_change_without_misleading_percentage(tmp_path):
    service, _ = cash_fixture(tmp_path, prior_capex=100)
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_cashflow("AAPL", period="annual")
    assert result["found"] and result["growth_id"] is None
    assert Decimal(tools.ledger[result["change_id"]]["value"]) == -10
    assert any("负数" in warning for warning in result["warnings"])


def test_net_receipts_are_bound_and_not_silently_assumed_zero(tmp_path):
    service, filing = cash_fixture(tmp_path, ticker="AMZN")
    html = (
        inline_html(
            concept="amzn:ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives", value="10"
        )
        .replace(b"0000320193", b"0001018724")
        .replace(b'scale="6"', b'scale="0"')
    )
    receipts = extract_cash_facts(html, filing)
    assert len(receipts) == 1
    service.corpus.add_facts(filing, receipts)
    tools = service.tools(date(2026, 10, 8))
    result = tools.get_cashflow("AMZN", period="annual")
    assert len(tools.ledger[result["current_id"]]["inputs"]) == 3
    assert Decimal(tools.ledger[result["current_id"]]["value"]) == -10
    assert any("上年口径不可比" in warning for warning in result["warnings"])
    with service.corpus.connect() as db:
        db.execute("DELETE FROM facts WHERE metric='ppe_sale_incentive_proceeds'")
    result = service.tools(date(2026, 10, 8)).get_cashflow("AMZN", period="annual")
    assert result["found"] and any("缺值当零" in warning for warning in result["warnings"])


def test_new_annual_period_wins_over_later_filed_old_quarter(tmp_path):
    service, filing = cash_fixture(tmp_path)
    quarter = filing.model_copy(
        update={
            "id": "old-quarter",
            "form": "10-Q",
            "filed": date(2026, 8, 1),
            "period_end": date(2026, 3, 31),
        }
    )
    service.corpus.ingest(quarter, [], [])
    result = service.tools(date(2026, 10, 8)).get_cashflow("AAPL")
    assert result["found"] and result["filing"]["id"] == filing.id


def test_annual_refresh_network_failure_retains_cached_report(tmp_path, monkeypatch):
    service, _ = cash_fixture(tmp_path)

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("fixture connection failure")

    monkeypatch.setattr("filingcopilot.discovery.acquire_company", unavailable)
    result = service.discovery.refresh_annual("AAPL", date(2026, 10, 8))
    assert result["found"] and "最新年报检查未完成" in result["warning"]


def test_nested_cashflow_package_recomputes_after_checksum_rebuilt(tmp_path):
    service, _ = cash_fixture(tmp_path)
    answer = service.research(ResearchRequest(language="zh", question="年度自由现金流同比是多少？", ticker="AAPL"))
    package = service.export_package(answer.id)
    nested = next(e for e in package["research"]["evidence"] if e.get("operation") == "difference")
    nested["value"] = "1000"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"
