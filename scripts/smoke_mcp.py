import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


async def check(read, write, transport):
    async with ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        names = [t.name for t in listed.tools]
        research_schema = next(t for t in listed.tools if t.name == "ask_financial_question").inputSchema
        assert research_schema["properties"]["language"]["default"] == "en"
        search_schema = next(t for t in listed.tools if t.name == "search_passages").inputSchema
        assert search_schema["properties"]["lexical_only"]["type"] == "boolean"
        compare_schema = next(t for t in listed.tools if t.name == "compare_companies").inputSchema
        assert compare_schema["properties"]["include_ttm_valuation"]["type"] == "boolean"
        assert compare_schema["properties"]["include_ttm_valuation"]["default"] is False
        expected_tools = {
            "get_watchlist",
            "compare_companies",
            "get_financial_trend",
            "find_stock",
            "get_stock_market",
            "get_stock_valuation",
            "valuation_scenarios",
            "get_provider_ttm_valuation",
            "get_historical_valuation",
            "get_cashflow",
            "get_ttm_financial_fact",
            "get_ttm_margin",
            "get_company_updates",
            "prepare_quarterly",
            "get_quarterly_overview",
            "get_quarterly_fact",
            "get_quarterly_sequence",
            "get_statement",
            "find_company",
            "prepare_company",
            "list_filings",
            "get_financial_fact",
            "get_company_overview",
            "search_passages",
            "calculate",
            "ask_financial_question",
            "get_fact_versions",
            "get_peer_candidates",
            "export_research_package",
            "audit_research_package",
        }
        if set(names) != expected_tools:
            raise AssertionError(
                "MCP tool registry mismatch: "
                f"missing={sorted(expected_tools - set(names))}, "
                f"extra={sorted(set(names) - expected_tools)}"
            )
        resource = await session.read_resource("filingcopilot://catalog")
        watchlist = await session.call_tool("get_watchlist", {})
        assert not watchlist.isError
        watchlist_data = watchlist.structuredContent or json.loads(watchlist.content[0].text)
        assert watchlist_data["items"] == [] and watchlist_data["limit"] == 30
        assert resource.contents
        prompts = await session.list_prompts()
        assert "company_research" in [p.name for p in prompts.prompts]
        stock = await session.call_tool("find_stock", {"query": "苹果"})
        assert not stock.isError
        stock_data = stock.structuredContent or json.loads(stock.content[0].text)
        assert stock_data["candidates"][0]["ticker"] == "AAPL"
        prices = await session.call_tool(
            "get_stock_market", {"ticker": "AAPL", "as_of": "2026-10-08", "period": "5Y"}
        )
        assert not prices.isError
        price_data = prices.structuredContent or json.loads(prices.content[0].text)
        assert price_data["found"] and price_data["statistics"]["trading_days"] == 3
        assert price_data["evidence"][0]["kind"] == "market"
        trend = await session.call_tool(
            "get_financial_trend",
            {"ticker": "AAPL", "as_of": "2026-10-08", "years": 3, "refresh_sources": False},
        )
        assert not trend.isError
        trend_data = trend.structuredContent or json.loads(trend.content[0].text)
        assert trend_data["mode"] == "trend" and trend_data["usage"]["model_calls"] == 0
        trend_audit = await session.call_tool(
            "audit_research_package", {"run_id": trend_data["id"]}
        )
        trend_report = trend_audit.structuredContent or json.loads(trend_audit.content[0].text)
        assert not trend_audit.isError and trend_report["status"] == "consistent"
        compared = await session.call_tool(
            "compare_companies",
            {
                "tickers": ["AAPL", "MSFT"],
                "as_of": "2026-10-08",
                "period": "5Y",
                "refresh_sources": False,
            },
        )
        assert not compared.isError
        comparison = compared.structuredContent or json.loads(compared.content[0].text)
        assert comparison["mode"] == "comparison" and comparison["usage"]["model_calls"] == 0
        assert comparison["trace"][0]["result"]["chart"]["common_days"] == 3
        comparison_audit = await session.call_tool(
            "audit_research_package", {"run_id": comparison["id"]}
        )
        assert not comparison_audit.isError
        compared_report = comparison_audit.structuredContent or json.loads(
            comparison_audit.content[0].text
        )
        assert compared_report["status"] == "consistent"
        benchmark = await session.call_tool(
            "compare_companies",
            {
                "tickers": ["AAPL"],
                "benchmark": "SPY",
                "as_of": "2026-10-08",
                "period": "5Y",
                "refresh_sources": False,
            },
        )
        assert not benchmark.isError
        benchmark_data = benchmark.structuredContent or json.loads(benchmark.content[0].text)
        benchmark_chart = benchmark_data["trace"][0]["result"]["chart"]
        assert (
            len(benchmark_chart["series"]) == 2
            and benchmark_chart["relative"][0]["benchmark"] == "SPY"
        )
        checked_benchmark = await session.call_tool(
            "audit_research_package", {"run_id": benchmark_data["id"]}
        )
        benchmark_report = checked_benchmark.structuredContent or json.loads(
            checked_benchmark.content[0].text
        )
        assert not checked_benchmark.isError and benchmark_report["status"] == "consistent"
        valuation = await session.call_tool(
            "get_stock_valuation", {"ticker": "AAPL", "as_of": "2026-10-08"}
        )
        assert not valuation.isError
        valuation_data = valuation.structuredContent or json.loads(valuation.content[0].text)
        assert not valuation_data["found"]  # Bundled annual demo intentionally has no EPS fixture.
        supplier = await session.call_tool(
            "get_provider_ttm_valuation",
            {
                "ticker": "AAPL",
                "as_of": date.today().isoformat(),
                "include_pe": False,
            },
        )
        assert not supplier.isError
        supplier_data = supplier.structuredContent or json.loads(supplier.content[0].text)
        assert supplier_data["found"] and supplier_data["evidence"]["kind"] == "valuation"
        assert supplier_data["evidence"]["snapshot"]["value"] == "4"
        ttm = await session.call_tool(
            "compare_companies",
            {
                "tickers": ["AAPL", "MSFT"],
                "benchmark": "SPY",
                "as_of": date.today().isoformat(),
                "refresh_sources": False,
                "include_ttm_valuation": True,
            },
        )
        assert not ttm.isError
        ttm_data = ttm.structuredContent or json.loads(ttm.content[0].text)
        compared_ttm = ttm_data["trace"][0]["result"]
        assert compared_ttm["schema_version"] == "filingcopilot.company-comparison/v3"
        assert compared_ttm["include_ttm_valuation"] is True
        ttm_ledger = {e["id"]: e for e in ttm_data["evidence"]}
        for row, expected in zip(compared_ttm["rows"], ("30", "15")):
            pe = ttm_ledger[row["cells"]["provider_ttm_pe"]]
            assert pe["value"] == expected
            assert pe["input_ids"] == [row["market_id"], row["cells"]["provider_ttm_eps"]]
        ttm_audit = await session.call_tool("audit_research_package", {"run_id": ttm_data["id"]})
        ttm_report = ttm_audit.structuredContent or json.loads(ttm_audit.content[0].text)
        assert not ttm_audit.isError and ttm_report["status"] == "consistent"
        scenarios = await session.call_tool(
            "valuation_scenarios",
            {
                "ticker": "AAPL",
                "as_of": date.today().isoformat(),
                "basis": "provider_ttm",
                "discount_pct": "10",
                "years": 3,
                "cases": [
                    {"name": "low", "growth_pct": "0", "terminal_pe": "10"},
                    {"name": "high", "growth_pct": "10", "terminal_pe": "20"},
                ],
            },
        )
        assert not scenarios.isError
        scenario_data = scenarios.structuredContent or json.loads(scenarios.content[0].text)
        assert scenario_data["mode"] == "scenarios" and scenario_data["usage"]["model_calls"] == 0
        scenario_audit = await session.call_tool(
            "audit_research_package", {"run_id": scenario_data["id"]}
        )
        scenario_checked = scenario_audit.structuredContent or json.loads(
            scenario_audit.content[0].text
        )
        assert not scenario_audit.isError and scenario_checked["status"] == "consistent"
        report = await session.call_tool(
            "ask_financial_question",
            {
                "question": "完整公司股票研究",
                "ticker": "AAPL",
                "as_of": "2026-10-08",
                "mode": "reference",
                "depth": "full",
                "auto_acquire": False,
            },
        )
        assert not report.isError
        report_data = report.structuredContent or json.loads(report.content[0].text)
        layout = next(
            t["sections"] for t in report_data["trace"] if t["stage"] == "company_report_layout"
        )
        assert report_data["mode"] == "company_report" and len(layout) == 6
        report_audit = await session.call_tool(
            "audit_research_package", {"run_id": report_data["id"]}
        )
        report_checked = report_audit.structuredContent or json.loads(report_audit.content[0].text)
        assert not report_audit.isError and report_checked["status"] == "consistent"
        company = await session.call_tool("find_company", {"query": "苹果", "translate": False})
        assert not company.isError
        located = company.structuredContent or json.loads(company.content[0].text)
        assert located["candidates"][0]["ticker"] == "AAPL"
        ready = await session.call_tool(
            "prepare_company", {"ticker": "AAPL", "as_of": "2026-10-08"}
        )
        assert not ready.isError
        prepared = ready.structuredContent or json.loads(ready.content[0].text)
        assert prepared["status"] == "ready" and prepared["source"] == "local"
        result = await session.call_tool(
            "get_financial_fact",
            {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025, "as_of": "2026-10-08"},
        )
        assert not result.isError
        data = result.structuredContent or json.loads(result.content[0].text)
        assert data["evidence"]["value"] == "416161000000"
        source_search = await session.call_tool(
            "search_passages",
            {
                "query": "risk operations",
                "ticker": "AAPL",
                "as_of": "2026-10-08",
                "lexical_only": True,
            },
        )
        assert not source_search.isError
        source_data = source_search.structuredContent or json.loads(source_search.content[0].text)
        assert source_data["backend"] == "fts5" and source_data["eligible_passages"] > 0
        trailing = await session.call_tool(
            "get_ttm_financial_fact", {"ticker": "AAPL", "metric": "revenue", "as_of": "2026-02-01"}
        )
        assert not trailing.isError
        trailing_data = trailing.structuredContent or json.loads(trailing.content[0].text)
        assert trailing_data["found"] and trailing_data["evidence"]["operation"] == "trailing_sum"
        assert len(trailing_data["evidence"]["inputs"]) == 1
        margin = await session.call_tool(
            "get_ttm_margin", {"ticker": "AAPL", "ratio": "net_margin", "as_of": "2026-02-01"}
        )
        assert not margin.isError
        margin_data = margin.structuredContent or json.loads(margin.content[0].text)
        assert margin_data["found"] and margin_data["evidence"]["operation"] == "ratio"
        assert all(e["operation"] == "trailing_sum" for e in margin_data["evidence"]["inputs"])
        trailing_calc = await session.call_tool(
            "calculate",
            {
                "operation": "trailing_sum",
                "left_id": data["evidence"]["id"],
                "right_id": None,
                "as_of": "2026-02-01",
            },
        )
        assert not trailing_calc.isError
        trailing_calc_data = trailing_calc.structuredContent or json.loads(
            trailing_calc.content[0].text
        )
        assert trailing_calc_data["evidence"]["value"] == data["evidence"]["value"]
        updates = await session.call_tool(
            "get_company_updates", {"ticker": "AAPL", "as_of": "2026-10-08", "refresh": False}
        )
        assert not updates.isError
        updates_data = updates.structuredContent or json.loads(updates.content[0].text)
        assert updates_data["found"] and len(updates_data["observed_evidence"]) == 1
        assert updates_data["events"][0]["filed"] == "2026-09-01"
        assert updates_data["observed_evidence"][0]["kind"] == "passage"
        assert "8-K" in updates_data["scope"]
        foreign_updates = await session.call_tool(
            "get_company_updates", {"ticker": "TSM", "as_of": "2026-10-08", "refresh": False}
        )
        assert not foreign_updates.isError
        foreign_data = foreign_updates.structuredContent or json.loads(
            foreign_updates.content[0].text
        )
        assert foreign_data["found"] and len(foreign_data["observed_evidence"]) == 1
        assert foreign_data["events"][0]["event_date"] is None
        assert foreign_data["events"][0]["report_date"] == "2026-09-30"
        assert "6-K" in foreign_data["scope"] and "8-K" not in foreign_data["scope"]
        overview = await session.call_tool(
            "get_company_overview", {"ticker": "AAPL", "as_of": "2026-10-08"}
        )
        assert not overview.isError
        summary = overview.structuredContent or json.loads(overview.content[0].text)
        assert summary["found"] and summary["fiscal_year"] == 2025 and summary["coverage_gaps"]
        quarterly = await session.call_tool(
            "get_quarterly_overview", {"ticker": "AAPL", "as_of": "2026-10-08"}
        )
        assert not quarterly.isError
        quarterly_data = quarterly.structuredContent or json.loads(quarterly.content[0].text)
        assert quarterly_data["found"] and quarterly_data["period_end"] == "2026-06-27"
        quarterly_fact = await session.call_tool(
            "get_quarterly_fact",
            {
                "ticker": "AAPL",
                "metric": "revenue",
                "period_end": "2026-06-27",
                "as_of": "2026-10-08",
            },
        )
        assert not quarterly_fact.isError
        quarterly_fact_data = quarterly_fact.structuredContent or json.loads(
            quarterly_fact.content[0].text
        )
        assert quarterly_fact_data["evidence"]["value"] == "120"
        sequence = await session.call_tool(
            "get_quarterly_sequence",
            {"ticker": "AAPL", "as_of": "2026-10-08", "metrics": ["revenue"], "quarters": 8},
        )
        assert not sequence.isError
        statement = await session.call_tool(
            "get_statement",
            {"ticker": "AAPL", "as_of": "2026-10-08", "statement": "all", "period_end": "2026-06-27"},
        )
        assert not statement.isError
        cashflow = await session.call_tool(
            "get_cashflow", {"ticker": "AAPL", "as_of": "2026-10-08"}
        )
        assert not cashflow.isError
        cashflow_data = cashflow.structuredContent or json.loads(cashflow.content[0].text)
        assert cashflow_data["found"] and cashflow_data["period_scope"] == "ytd"
        cash_result = next(
            e for e in cashflow_data["observed_evidence"] if e["id"] == cashflow_data["current_id"]
        )
        assert cash_result["value"] == "60" and len(cash_result["inputs"]) == 2
        prior = await session.call_tool(
            "get_financial_fact",
            {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2024, "as_of": "2026-10-08"},
        )
        previous = prior.structuredContent or json.loads(prior.content[0].text)
        calculation = await session.call_tool(
            "calculate",
            {
                "operation": "growth",
                "left_id": data["evidence"]["id"],
                "right_id": previous["evidence"]["id"],
                "as_of": "2026-10-08",
            },
        )
        assert not calculation.isError
        net = await session.call_tool(
            "calculate",
            {
                "operation": "net_cash_capex",
                "left_id": "AMZN:synthetic-gross",
                "right_id": "AMZN:synthetic-receipts",
                "as_of": "2026-10-08",
            },
        )
        assert not net.isError
        net_data = net.structuredContent or json.loads(net.content[0].text)
        assert net_data["evidence"]["value"] == "80"
        assert net_data["evidence"]["formula"] == "cash_capex - ppe_sale_incentive_proceeds"
        assert len(net_data["evidence"]["inputs"]) == 2
        mixed = await session.call_tool(
            "calculate",
            {
                "operation": "net_cash_capex",
                "left_id": data["evidence"]["id"],
                "right_id": "AMZN:synthetic-receipts",
                "as_of": "2026-10-08",
            },
        )
        assert mixed.isError  # Other company's revenue cannot become net PP&E purchases.
        question = await session.call_tool(
            "ask_financial_question",
            {
                "question": "苹果 FY2025 营收是多少？",
                "ticker": "AAPL",
                "as_of": "2026-10-08",
                "mode": "reference",
            },
        )
        assert not question.isError
        answer = question.structuredContent or json.loads(question.content[0].text)
        assert answer["language"] == "en" and any("Revenue" in c["text"] for c in answer["answer"])
        chinese_question = await session.call_tool(
            "ask_financial_question",
            {"question": "苹果 FY2025 营收是多少？", "ticker": "AAPL",
             "as_of": "2026-10-08", "mode": "reference", "language": "zh"},
        )
        assert not chinese_question.isError
        chinese_answer = chinese_question.structuredContent or json.loads(chinese_question.content[0].text)
        assert chinese_answer["language"] == "zh"
        assert any("营业收入" in c["text"] for c in chinese_answer["answer"])
        assert chinese_answer["evidence"] == answer["evidence"]
        package = await session.call_tool("export_research_package", {"run_id": answer["id"]})
        assert not package.isError
        exported = package.structuredContent or json.loads(package.content[0].text)
        assert exported["schema_version"] == "filingcopilot.research-package/v1"
        audit = await session.call_tool("audit_research_package", {"run_id": answer["id"]})
        assert not audit.isError
        reviewed = audit.structuredContent or json.loads(audit.content[0].text)
        assert reviewed["status"] == "consistent" and reviewed["source_validation"] == "matched"
        versions = await session.call_tool(
            "get_fact_versions",
            {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2024, "as_of": "2026-10-08"},
        )
        assert not versions.isError
        return {
            "transport": transport,
            "initialize": "passed",
            "tools": names,
            "resource": "passed",
            "prompt": "passed",
            "fact_call": "passed",
            "lexical_source_search": "passed (schema and FTS-only call; local filing fixture)",
            "calculation_call": "passed",
            "net_ppe_calculation": "passed (synthetic exact-period AMZN facts; mixed-company inputs rejected; no network)",
            "research_call": "passed",
            "research_languages": "passed (English default and explicit Chinese share the same source evidence)",
            "package_export": "passed",
            "offline_audit": "passed",
            "version_comparison": "passed",
            "company_lookup": "passed",
            "company_preparation": "passed",
            "company_overview": "passed",
            "stock_lookup": "passed",
            "market_call": "passed (synthetic cached daily prices; no network)",
            "financial_trend": "passed (synthetic annual windows and saved offline replay; no network/model)",
            "watchlist": "passed (empty persisted preferences; read only; no network/model)",
            "company_comparison": "passed (two synthetic series, annual facts and offline chart replay; no network/model)",
            "benchmark_comparison": "passed (one listing plus cached ETF, relative percentage points and offline replay; no network/model)",
            "valuation_missing_eps": "passed",
            "provider_ttm_eps": "passed (synthetic supplier snapshot, current date; no network)",
            "comparison_ttm": "passed (v3 opt-in, same-column quote/EPS, two distinct multiples, ETF excluded, offline replay; synthetic/no network/model)",
            "quarterly_overview": "passed (synthetic quarter and comparative; no network)",
            "quarterly_fact": "passed (exact source period; synthetic)",
            "quarterly_sequence": "passed (rolling sequence endpoint; synthetic)",
            "statement_rows": "passed (structured statement endpoint; synthetic)",
            "cashflow": "passed (synthetic same-period cash OCF/PP&E and nested comparison; no network)",
            "ttm_flow": "passed (synthetic frozen annual flow, actual source window; no network)",
            "ttm_margin": "passed (synthetic aligned trailing profit/revenue; no network)",
            "company_updates": "passed (synthetic dated 8-K excerpt, refresh=False; no network)",
            "foreign_company_updates": "passed (synthetic 6-K excerpt, public/report dates distinct, refresh=False; no network)",
        }


async def main(home: Path, output: Path):
    started = time.perf_counter()
    from filingcopilot.corpus import Corpus
    from filingcopilot.ingestion import extract_facts, extract_passages
    from filingcopilot.market import MarketData
    from filingcopilot.models import Fact, Filing

    corpus = Corpus(home)
    corpus.seed()
    from filingcopilot.valuation import parse_eps

    market = MarketData(home)
    at = datetime.now().astimezone()
    eps_url = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL?fixture=synthetic&type=trailingDilutedEPS"
    eps_raw = json.dumps(
        {
            "timeseries": {
                "result": [
                    {
                        "meta": {"symbol": ["AAPL"], "type": ["trailingDilutedEPS"]},
                        "trailingDilutedEPS": [
                            {
                                "asOfDate": (date.today() - timedelta(days=60)).isoformat(),
                                "periodType": "TTM",
                                "currencyCode": "USD",
                                "reportedValue": {"raw": 4},
                            }
                        ],
                    }
                ],
                "error": None,
            }
        }
    ).encode()
    market.valuations._save("AAPL", parse_eps(eps_raw, "AAPL", at, eps_url), eps_raw)
    msft_eps = json.loads(eps_raw)
    msft_eps["timeseries"]["result"][0]["meta"]["symbol"] = ["MSFT"]
    msft_eps["timeseries"]["result"][0]["trailingDilutedEPS"][0]["reportedValue"]["raw"] = 8
    msft_raw = json.dumps(msft_eps).encode()
    msft_url = eps_url.replace("/AAPL?", "/MSFT?")
    market.valuations._save("MSFT", parse_eps(msft_raw, "MSFT", at, msft_url), msft_raw)
    notice = b"<html><p>This is a synthetic announcement fixture. Apple announced a new service and described recent product updates and customer availability. It is used only to verify the MCP interface and date filtering.</p></html>"
    notice_filing = Filing(
        id="AAPL:synthetic-notice",
        ticker="AAPL",
        company="Synthetic MCP notice fixture",
        cik="0000320193",
        accession="synthetic-notice",
        form="8-K",
        filed="2026-09-01",
        period_end="2026-08-31",
        source_url="https://www.sec.gov/Archives/synthetic-notice.htm",
        content_sha256=hashlib.sha256(notice).hexdigest(),
    )
    corpus.ingest(notice_filing, [], extract_passages(notice, notice_filing))
    foreign_notice = b"<p>This is a synthetic foreign issuer announcement. TSMC announced monthly consolidated revenue and described the reporting period separately from the public date. This fixture checks the MCP interface without live sources.</p>"
    foreign_filing = Filing(
        id="TSM:synthetic-notice",
        ticker="TSM",
        company="Synthetic MCP foreign fixture",
        cik="0001046179",
        accession="synthetic-foreign-notice",
        form="6-K",
        filed="2026-10-08",
        period_end="2026-09-30",
        source_url="https://www.sec.gov/Archives/synthetic-foreign-notice.htm",
        content_sha256=hashlib.sha256(foreign_notice).hexdigest(),
    )
    corpus.ingest(foreign_filing, [], extract_passages(foreign_notice, foreign_filing))
    filing = Filing(
        id="AAPL:synthetic-quarter",
        ticker="AAPL",
        company="Synthetic MCP quarter fixture",
        cik="0000320193",
        accession="synthetic-quarter",
        form="10-Q",
        filed="2026-07-30",
        period_end="2026-06-27",
        source_url="https://www.sec.gov/Archives/synthetic-quarter.htm",
        content_sha256="a" * 64,
    )
    content = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": filing.accession,
                                "form": "10-Q",
                                "start": "2026-03-29",
                                "end": "2026-06-27",
                                "val": 120,
                            },
                            {
                                "accn": filing.accession,
                                "form": "10-Q",
                                "start": "2025-03-30",
                                "end": "2025-06-28",
                                "val": 100,
                            },
                        ]
                    }
                }
            }
        }
    }
    for concept, values in [
        ("NetCashProvidedByUsedInOperatingActivities", (100, 90)),
        ("PaymentsToAcquirePropertyPlantAndEquipment", (40, 50)),
    ]:
        content["facts"]["us-gaap"][concept] = {
            "units": {
                "USD": [
                    {
                        "accn": filing.accession,
                        "form": "10-Q",
                        "start": start,
                        "end": end,
                        "val": value,
                    }
                    for start, end, value in [
                        ("2026-01-01", "2026-06-27", values[0]),
                        ("2025-01-01", "2025-06-28", values[1]),
                    ]
                ]
            }
        }
    corpus.ingest(filing, extract_facts(content, filing), [])
    net_filing = filing.model_copy(
        update={
            "id": "AMZN:synthetic-net",
            "ticker": "AMZN",
            "cik": "0001018724",
            "company": "Synthetic MCP net-purchase fixture",
            "period_end": date(2026, 6, 30),
            "source_url": "https://www.sec.gov/Archives/synthetic-net.htm",
        }
    )
    net_facts = [
        Fact(
            id="AMZN:synthetic-" + suffix,
            filing_id=net_filing.id,
            ticker="AMZN",
            metric=metric,
            label=label,
            concept=concept,
            value=value,
            unit="USD",
            fiscal_year=2026,
            start="2026-01-01",
            end="2026-06-30",
            source_url=net_filing.source_url,
            locator="synthetic transport fixture, not a real SEC source",
        )
        for suffix, metric, label, concept, value in [
            (
                "gross",
                "cash_capex",
                "现金PP&E购置毛额",
                "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
                100,
            ),
            (
                "receipts",
                "ppe_sale_incentive_proceeds",
                "出售及激励收款",
                "amzn:ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives",
                20,
            ),
        ]
    ]
    corpus.ingest(net_filing, net_facts, [])

    stamps = [
        int(datetime(2026, 10, day, 16, tzinfo=timezone.utc).timestamp()) for day in (5, 6, 7)
    ]
    fixture = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "AAPL",
                        "currency": "USD",
                        "instrumentType": "EQUITY",
                        "exchangeTimezoneName": "UTC",
                        "regularMarketPrice": 120,
                        "regularMarketTime": int(datetime.now(timezone.utc).timestamp()),
                    },
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{"close": [100, 80, 120]}],
                        "adjclose": [{"adjclose": [100, 80, 120]}],
                    },
                }
            ]
        }
    }
    MarketData(home)._save(
        "AAPL",
        json.dumps(fixture).encode(),
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?fixture=synthetic",
        datetime.now(timezone.utc),
    )
    fixture["chart"]["result"][0]["meta"]["symbol"] = "MSFT"
    MarketData(home)._save(
        "MSFT",
        json.dumps(fixture).encode(),
        "https://query1.finance.yahoo.com/v8/finance/chart/MSFT?fixture=synthetic",
        datetime.now(timezone.utc),
    )
    fixture["chart"]["result"][0]["meta"].update(symbol="SPY", instrumentType="ETF")
    MarketData(home)._save(
        "SPY",
        json.dumps(fixture).encode(),
        "https://query1.finance.yahoo.com/v8/finance/chart/SPY?fixture=synthetic",
        datetime.now(timezone.utc),
    )
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "FC_EMBEDDING_MODE": "off",
        "NO_PROXY": "127.0.0.1,localhost",
        "FC_API_BASE": "",
        "FC_API_KEY": "",
        "FC_MODEL": "",
        "FC_REVIEW_MODEL": "",
        "FC_DOMAIN_CONFIG": "",
        "FC_SEC_USER_AGENT": "",
    }
    args = ["-m", "filingcopilot", "--home", str(home), "mcp"]
    params = StdioServerParameters(command=sys.executable, args=args, cwd=str(root), env=env)
    async with stdio_client(params) as (read, write):
        stdio = await check(read, write, "stdio")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, *args, "--transport", "streamable-http", "--port", str(port)],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("MCP HTTP server exited before readiness")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("MCP HTTP server did not become ready")
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
            http = await check(read, write, "streamable-http")
    finally:
        process.terminate()
        process.wait(timeout=10)
    report = {
        "checks": [stdio, http],
        "seconds": round(time.perf_counter() - started, 3),
        "timing_scope": "Fixture setup and both transports; parent Python startup/import time excluded.",
        "scope": "Isolated temporary corpus and cached synthetic supplier data; no live supplier or research model assessment.",
    }
    target = output
    report["output"] = str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Also refuse a path created by another runner after the initial CLI check.
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Report path; defaults to a new UTC-timestamped artifact.")
    options = parser.parse_args()
    target = options.output or (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / f"mcp-smoke-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
    )
    if target.exists():
        parser.error("Explicit output already exists; retain the previous acceptance record.")
    with tempfile.TemporaryDirectory(prefix="filingcopilot-mcp-") as directory:
        asyncio.run(main(Path(directory), target))
