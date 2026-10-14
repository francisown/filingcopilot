from __future__ import annotations

import json
from datetime import date

from mcp.server.fastmcp import FastMCP

from .comparison import ComparisonRequest
from .models import ResearchRequest
from .service import FilingCopilot
from .settings import Settings
from .trends import TrendRequest


def create_mcp(service: FilingCopilot | None = None, port: int = 8124) -> FastMCP:
    service = service or FilingCopilot(Settings.from_env())
    mcp = FastMCP(
        "FilingCopilot",
        instructions="Evidence-grounded annual, rolling-quarter and complete statement research. "
        "Use as_of to prevent future information. Arithmetic must use observed facts; missing rows stay missing.",
        host="127.0.0.1",
        port=port,
    )

    @mcp.tool()
    def get_watchlist() -> dict:
        """Read locally saved listing identities and current research capabilities.
        No prices, holdings, transactions, source downloads or model calls. Use
        get_stock_market for quotes and history for a selected listing."""
        return service.watchlist.items()

    @mcp.tool()
    def get_company_updates(ticker: str, as_of: str, refresh: bool = True) -> dict:
        """Read preceding-90-day official SEC 8-K/6-K notices, HKEXnews announcements,
        and registered CNINFO A-share announcements. SEC accessions, HKEX English/Chinese
        documents, and CNINFO Chinese documents stay separate from registered
        transcript/news feeds, with source levels and public/first-observed availability.
        HKEX items are official exchange disclosures, not SEC filings or typed financial
        facts; raw documents have bound hashes."""
        day = date.fromisoformat(as_of)
        listing = service.market.directory.listing(ticker)
        from .updates import update_forms

        prepared = (
            service.discovery.prepare_updates(ticker, day)
            if refresh and update_forms(listing)
            else {"checked": False, "reason": "该证券没有 SEC 8-K/6-K 申报来源；使用登记的市场公告来源。"}
            if refresh
            else None
        )
        external = service.discovery.prepare_external_updates(ticker, day) if refresh else None
        tools = service.tools(day)
        return {
            **tools.get_company_updates(ticker),
            "preparation": prepared,
            "external_preparation": external,
            "observed_evidence": list(tools.ledger.values()),
        }

    @mcp.tool()
    def get_cashflow(
        ticker: str, as_of: str, period: str = "latest", fiscal_year: int | None = None
    ) -> dict:
        """Read local source-bound cash PP&E and project-calculated FCF. Latest picks the
        newer 10-K/10-Q, keeps quarter/YTD distinct, and includes raw facts/formulas. Use
        ask_financial_question for automatic disclosure refresh before the calculation."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_cashflow(ticker, period, fiscal_year)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_ttm_financial_fact(ticker: str, metric: str, as_of: str) -> dict:
        """Read source-bound trailing financial flows with fiscal-date checks and recursive
        provenance. Supports additive metrics, not EPS, balances or margins. Call
        ask_financial_question for automatic annual/quarterly acquisition first."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_ttm_financial_fact(ticker, metric)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_ttm_margin(ticker: str, ratio: str, as_of: str) -> dict:
        """Compute gross_margin, operating_margin or net_margin from source-bound TTM
        profit / revenue with matching fiscal periods, currency and filing versions.
        Call ask_financial_question to acquire annual/quarterly disclosures first."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_ttm_margin(ticker, ratio)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_stock_valuation(ticker: str, as_of: str, fiscal_year: int | None = None) -> dict:
        """Compute observed price / disclosed annual diluted EPS. Explicitly annual, not TTM
        or forward P/E; acquires missing annual sources within a short time budget,
        verifies company/currency/share basis and returns bound evidence and preparation status."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_stock_valuation(ticker, fiscal_year)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_provider_ttm_valuation(ticker: str, as_of: str, include_pe: bool = True) -> dict:
        """Read Yahoo supplier TTM diluted EPS and optionally price/EPS. Current date only.
        Calendar-normalized period labels, not reconstructed SEC weighted shares or forward EPS.
        Raw snapshots are separately bound; negative/stale/mismatched inputs withhold P/E."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_provider_ttm_valuation(ticker, include_pe)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_historical_valuation(ticker: str, as_of: str, period: str = "5Y") -> dict:
        """Rebuild a conservative historical annual P/E reference from daily prices
        and annual diluted EPS that was public before each trading day. Missing,
        stale, currency, share-basis and later-split points remain gaps; this is
        not historical TTM/forward P/E or a cheap/expensive conclusion."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_historical_valuation(ticker, period)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def prepare_quarterly(ticker: str, as_of: str) -> dict:
        """Check SEC for the latest 10-Q before as_of, retain raw responses and local evidence
        on network failure. A warning means freshness was not confirmed."""
        return service.discovery.prepare_quarterly(ticker, date.fromisoformat(as_of))

    @mcp.tool()
    def get_quarterly_overview(ticker: str, as_of: str, period: str = "quarter") -> dict:
        """Read latest local 10-Q quarter/YTD facts and code-computed YoY/margins. Call
        prepare_quarterly first to check for new filings. Cash flow is separately dated."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_quarterly_overview(ticker, period)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_quarterly_fact(
        ticker: str, metric: str, period_end: str, as_of: str, period: str = "quarter"
    ) -> dict:
        """Read a source-disclosed 10-Q fact with exact dates and quarter or YTD scope."""
        return service.tools(date.fromisoformat(as_of)).get_quarterly_fact(
            ticker, metric, period_end, period
        )

    @mcp.tool()
    def get_quarterly_sequence(
        ticker: str, as_of: str, metrics: list[str] | None = None, quarters: int = 8
    ) -> dict:
        """Read up to twelve exact single-quarter periods from retained 10-Q facts.
        Missing periods remain explicit and are never filled with zero or calendar-quarter guesses."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_quarterly_sequence(ticker, metrics, quarters)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def get_statement(
        ticker: str,
        as_of: str,
        statement: str = "all",
        period_end: str | None = None,
        filing_id: str | None = None,
    ) -> dict:
        """Read structured income, balance-sheet or cash-flow rows, including
        unregistered XBRL labels and explicit segment/geography dimensions."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_statement(ticker, statement, period_end, filing_id)
        return {**result, "observed_evidence": list(tools.ledger.values())}

    @mcp.tool()
    def find_stock(query: str) -> dict:
        """Find stock/ETF/index symbols for multiple exchanges; distinguishes SEC filing scope."""
        return service.find_stock(query)

    @mcp.tool()
    def get_peer_candidates(ticker: str) -> dict:
        """Return transparent locally registered candidate peers for a ticker.
        This is a planning aid, not an authoritative sector classification or a
        fair-value ranking; only returned symbols are eligible for comparison."""
        return service.peer_candidates(ticker)

    @mcp.tool()
    def get_financial_trend(
        ticker: str, as_of: str, years: int = 5, refresh_sources: bool = True
    ) -> dict:
        """Save three/five complete annual windows, binding metrics and gross-PP&E FCF
        to each year's source version. Missing rows remain gaps; no FX or model calls.
        Return source references and a v1 package ID for offline replay."""
        return service.financial_trend(
            TrendRequest(
                ticker=ticker,
                as_of=date.fromisoformat(as_of),
                years=years,
                refresh_sources=refresh_sources,
            )
        ).model_dump(mode="json")

    @mcp.tool()
    def compare_companies(
        tickers: list[str],
        as_of: str,
        period: str = "5Y",
        refresh_sources: bool = True,
        benchmark: str | None = None,
        include_ttm_valuation: bool = False,
    ) -> dict:
        """Compare 2-4 explicit listings using a common observed-date rebased chart,
        annual growth/margins/gross-PP&E FCF and annual EPS P/E. Fiscal periods and
        currencies stay explicit. No model or currency conversion; partial failures
        preserve other companies. Optional SPY/QQQ ETF benchmark supports 1-4 selected
        listings, with same-window percentage-point differences; no index/alpha/FX claim.
        Optional include_ttm_valuation adds separately dated current supplier EPS/P-E
        using each column's quote (v3). Not automatic peer selection or fair-value ranking.
        Returns a saved exportable package ID; old v1/v2 stay readable."""
        return service.compare_companies(
            ComparisonRequest(
                tickers=tickers,
                benchmark=benchmark,
                as_of=date.fromisoformat(as_of),
                period=period,
                refresh_sources=refresh_sources,
                include_ttm_valuation=include_ttm_valuation,
            )
        ).model_dump(mode="json")

    @mcp.tool()
    def get_stock_market(ticker: str, as_of: str, period: str = "5Y") -> dict:
        """Read long-term price history with timestamps/currency/adjustment and code-computed
        range change and maximum drawdown. Quotes may be delayed. Does not call a model."""
        tools = service.tools(date.fromisoformat(as_of))
        result = tools.get_stock_market(ticker, period)
        return {**result, "evidence": list(tools.ledger.values())}

    @mcp.tool()
    def find_company(query: str, translate: bool = True) -> dict:
        """Resolve English name, ticker or CIK against SEC identities. Chinese conversion calls
        the configured model if translate=True. Multiple matches require explicit selection."""
        return service.discovery.search(query, translate)

    @mcp.tool()
    def prepare_company(ticker: str, as_of: str, years: int = 2) -> dict:
        """Download and index missing SEC 10-K annual HTML reports before as_of. Reuses local
        filings. Requires SEC contact configuration for uncached network acquisition."""
        return service.discovery.prepare(ticker, date.fromisoformat(as_of), years)

    @mcp.tool()
    def list_filings(as_of: str, ticker: str | None = None) -> dict:
        """List available annual filings and supported metrics before a knowledge date."""
        return service.tools(date.fromisoformat(as_of)).list_filings(ticker)

    @mcp.tool()
    def get_financial_fact(ticker: str, metric: str, fiscal_year: int, as_of: str) -> dict:
        """Read an annual fiscal-year fact with company, period, unit and SEC provenance."""
        return service.tools(date.fromisoformat(as_of)).get_financial_fact(
            ticker, metric, fiscal_year
        )

    @mcp.tool()
    def get_company_overview(ticker: str, as_of: str, fiscal_year: int | None = None) -> dict:
        """Read two annual periods and source-bound growth/margin calculations for a stock
        fundamentals assessment. Returns coverage gaps, without market quotes or valuation."""
        return service.tools(date.fromisoformat(as_of)).get_company_overview(ticker, fiscal_year)

    @mcp.tool()
    def get_fact_versions(ticker: str, metric: str, fiscal_year: int, as_of: str) -> dict:
        """Compare annual disclosure versions publicly available before as_of. Changed values
        alone do not establish a restatement cause; incompatible periods are not compared."""
        return service.tools(date.fromisoformat(as_of)).get_fact_versions(
            ticker, metric, fiscal_year
        )

    @mcp.tool()
    def export_research_package(run_id: str) -> dict:
        """Export a saved answer with frozen evidence/configuration and consistency checksum."""
        return service.export_package(run_id)

    @mcp.tool()
    def audit_research_package(run_id: str) -> dict:
        """Recompute a saved package and compare local evidence/cache without model calls.
        The result explicitly lists unchecked narrative semantics and missing raw sources."""
        return service.audit_run(run_id)

    @mcp.tool()
    def search_passages(
        query: str,
        as_of: str,
        ticker: str | None = None,
        k: int = 5,
        fiscal_year: int | None = None,
        filing_id: str | None = None,
        lexical_only: bool = False,
    ) -> dict:
        """RAG over filing narrative; lexical_only skips the optional embedding model."""
        return service.tools(date.fromisoformat(as_of)).search_passages(
            query, ticker, k, fiscal_year, filing_id, lexical_only
        )

    @mcp.tool()
    def calculate(
        operation: str,
        left_id: str,
        right_id: str | None,
        as_of: str,
        adjustment_id: str | None = None,
    ) -> dict:
        """Compute bounded arithmetic from corpus fact IDs, including trailing additive flows.
        For one full-year trailing flow, pass right_id=null. Use get_cashflow(period=ttm)
        for nested FCF and TTM net-purchase formulas; this endpoint accepts source facts only.
        net_cash_capex subtracts disclosed same-period AMZN sale/incentive receipts
        (right_id) from cash PP&E gross purchases (left_id), with audited company identity."""
        tools = service.tools(date.fromisoformat(as_of))
        for evidence_id in [left_id, right_id, *([adjustment_id] if adjustment_id else [])]:
            if evidence_id is None:
                continue
            evidence = service.corpus.evidence(evidence_id, tools.as_of)
            if evidence is None or evidence["kind"] != "fact":
                raise ValueError("Input fact unavailable before as_of")
            tools.ledger[evidence_id] = evidence
        return tools.calculate(operation, left_id, right_id, adjustment_id)

    @mcp.tool()
    def ask_financial_question(
        question: str,
        as_of: str,
        ticker: str | None = None,
        mode: str = "agent",
        company: str | None = None,
        auto_acquire: bool = False,
        depth: str = "brief",
        language: str = "en",
    ) -> dict:
        """Run bounded research with dated sources and verification. Plain numeric questions,
        including explicit 2-4 company TTM EPS/P-E comparisons, acquire sources and calculate
        without a model. Explanatory Agent research uses the configured model provider."""
        request = ResearchRequest(
            question=question,
            ticker=ticker,
            as_of=date.fromisoformat(as_of),
            mode=mode,
            company=company,
            auto_acquire=auto_acquire,
            depth=depth,
            language=language,
        )
        return service.research(request).model_dump(mode="json")

    @mcp.resource("filingcopilot://catalog")
    def catalog_resource() -> str:
        return json.dumps(service.corpus.catalog(date.today()), ensure_ascii=False)

    @mcp.tool()
    def valuation_scenarios(
        ticker: str,
        as_of: str,
        cases: list[dict],
        discount_pct: str,
        years: int = 3,
        basis: str = "annual",
    ) -> dict:
        """Compute explicit user EPS-growth and terminal-P/E scenarios with cited inputs.
        Cases require name, growth_pct, terminal_pe. These assumptions are not forecasts.
        Results and exact Decimal formulas are frozen for offline arithmetic checks."""
        from .scenarios import ScenarioRequest

        request = ScenarioRequest(
            ticker=ticker,
            as_of=as_of,
            cases=cases,
            discount_pct=discount_pct,
            years=years,
            basis=basis,
        )
        return service.valuation_scenarios(request).model_dump(mode="json")

    @mcp.prompt()
    def company_research(ticker: str, fiscal_year: int, as_of: str) -> str:
        return (
            f"研究 {ticker} FY{fiscal_year} 的营收、同比、利润率、管理层解释和主要风险。"
            f"仅使用 {as_of} 前公开的资料，所有数字通过工具获取和计算，逐项引用证据，"
            "缺失证据时明确说明。先列出可用申报，再使用研究工具。"
        )

    return mcp
