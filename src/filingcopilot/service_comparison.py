from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date

import httpx

from .audit import create_package
from .cash_sources import extract_cash_facts
from .cashflow import is_cashflow_question
from .comparison import (
    BENCHMARK_SCHEMA,
    VALUATION_SCHEMA,
    ComparisonRequest,
    collect_benchmark,
    collect_company,
    collect_comparison_valuation,
    comparison_chart,
    comparison_note,
    validate_comparison,
)
from .comparison import SCHEMA as COMPARISON_SCHEMA
from .ingestion import extract_facts
from .localization import prepare_answer, request_language
from .models import Claim, Filing, ResearchAnswer, Submission
from .peers import candidate_peers
from .trends import METRICS as TREND_METRICS
from .trends import TrendRequest, annual_trend, validate_trend
from .verification import verify_submission


class ComparisonMixin:
    @request_language
    def financial_trend(self, request: TrendRequest) -> ResearchAnswer:
        answer, domain = self._financial_trend(request)
        prepare_answer(answer, request.language)
        self.corpus.save_run(
            answer.model_dump(mode="json"), create_package(answer, domain, request.ticker)
        )
        return answer

    @request_language
    def valuation_scenarios(self, request) -> ResearchAnswer:
        from .scenarios import valuation_scenarios

        answer, domain, ticker = valuation_scenarios(self, request)
        prepare_answer(answer, request.language)
        self.corpus.save_run(answer.model_dump(mode="json"), create_package(answer, domain, ticker))
        return answer

    def _financial_trend(self, request: TrendRequest, question: str | None = None):
        started = time.monotonic()
        prepared = None
        if request.refresh_sources:
            prepared = self.discovery.prepare_financial_trend(
                request.ticker, request.as_of, request.years
            )
        self.enrich_cached_facts(request.ticker)
        tools = self.tools(request.as_of)
        result = annual_trend(tools, request)
        validate_trend(result, tools.ledger, tools.domain, request.as_of)
        question = (
            question
            or f"{request.ticker} 最近{request.years}个财年营业收入、营业利润、净利润、经营现金流和自由现金流趋势"
        )
        # The table is independently scope-checked by audit. Keep existing ten-claim limits.
        ids = [
            row["cells"][metric]
            for row in result["rows"][-2:]
            for metric in TREND_METRICS
            if metric in row["cells"]
        ]
        from .periods import fact_inputs

        requested_metrics = tools.domain.allowed_metrics(question)
        if is_cashflow_question(question):
            requested_metrics.update(
                {"operating_cash_flow", "cash_capex", "ppe_sale_incentive_proceeds"}
            )
        ids = [
            key
            for key in ids
            if not requested_metrics
            or {f["metric"] for f in fact_inputs(tools.ledger[key])} <= requested_metrics
        ]
        claims, gaps = verify_submission(
            Submission(
                claims=[Claim(kind=tools.ledger[key]["kind"], evidence_ids=[key]) for key in ids]
            ),
            tools,
            question,
        )
        if prepared and not prepared.get("checked"):
            gaps.append(prepared["warning"])
        elif not request.refresh_sources:
            gaps.append("本次沿用已保存年报，未联网检查最新披露；可点击刷新并重新获取。")
        if not result["rows"]:
            gaps.append("尚无此知悉日期前可用的年度财务；请检查证券的数据覆盖范围或重试自动准备。")
        elif any(row["missing"] for row in result["rows"]):
            gaps.append("部分年份或指标缺少同版本年度输入，图表与表格保留缺口；可刷新年报后重试。")
        gaps.extend(issue for row in result["rows"] for issue in row["issues"])
        answer = ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=question,
            as_of=request.as_of,
            mode="trend",
            status="partial" if gaps else "answered",
            answer=claims,
            gaps=list(dict.fromkeys(gaps)),
            evidence=list(tools.ledger.values()),
            trace=[
                {"stage": "financial_trend", "result": result},
                {"stage": "trend_sources", "preparation": prepared},
            ],
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return answer, tools.domain

    @request_language
    def compare_companies(self, request: ComparisonRequest) -> ResearchAnswer:
        answer, domain = self._compare_companies(request)
        prepare_answer(answer, request.language)
        self.corpus.save_run(answer.model_dump(mode="json"), create_package(answer, domain))
        return answer

    def _compare_companies(self, request: ComparisonRequest, *, question=None):
        """Prepare sources per company in parallel, then freeze a replayable comparison."""
        started = time.monotonic()

        def observe(ticker):
            preparation = None
            if request.refresh_sources:
                try:
                    preparation = self.discovery.prepare_valuation(ticker, request.as_of)
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    preparation = {"checked": False, "error_type": type(exc).__name__}
            tools = self.tools(request.as_of)
            try:
                row = collect_company(
                    tools, ticker, request.period, refresh_prices=request.refresh_prices
                )
            except (httpx.HTTPError, ValueError, OSError) as exc:
                # A failed company leaves the other observations usable. Never expose
                # request headers or an exception URL in a user-facing failure message.
                observed_market = next(
                    (
                        e["id"]
                        for e in tools.ledger.values()
                        if e["kind"] == "market" and e["ticker"] == ticker
                    ),
                    None,
                )
                row = {
                    "ticker": ticker,
                    "market_id": observed_market,
                    "annual_basis_id": None,
                    "cells": {},
                    "issues": [
                        {
                            "scope": "source",
                            "message": f"资料获取未完成（{type(exc).__name__}）；请重试或检查数据源连接。",
                        }
                    ],
                }
            if request.include_ttm_valuation:
                collect_comparison_valuation(tools, row)
            if preparation and not preparation.get("checked"):
                row["issues"].append(
                    {
                        "scope": "refresh",
                        "message": "年报最新版本检查未完成；已有数字保留，披露新鲜度尚未确认。请稍后重试。",
                    }
                )
            return row, tools.ledger, preparation

        def observe_benchmark():
            tools = self.tools(request.as_of)
            row = collect_benchmark(
                tools, request.benchmark, request.period, refresh_prices=request.refresh_prices
            )
            return row, tools.ledger, None

        with ThreadPoolExecutor(max_workers=len(request.tickers) + bool(request.benchmark)) as pool:
            tasks = [pool.submit(copy_context().run, observe, ticker) for ticker in request.tickers]
            benchmark_task = (
                pool.submit(copy_context().run, observe_benchmark) if request.benchmark else None
            )
            observations = [task.result() for task in tasks]
            benchmark_observation = benchmark_task.result() if benchmark_task else None
        ledger = {}
        for _, observed, _ in observations + (
            [benchmark_observation] if benchmark_observation else []
        ):
            for key, item in observed.items():
                if key in ledger and ledger[key] != item:
                    raise ValueError("公司对比出现冲突证据，请重新获取资料。")
                ledger[key] = item
        rows = [row for row, _, _ in observations]
        benchmark = benchmark_observation[0] if benchmark_observation else None
        result = {
            "schema_version": VALUATION_SCHEMA
            if request.include_ttm_valuation
            else BENCHMARK_SCHEMA
            if benchmark
            else COMPARISON_SCHEMA,
            "tickers": request.tickers,
            "period": request.period,
            "as_of": request.as_of.isoformat(),
            "rows": rows,
            "chart": comparison_chart(
                [r["market_id"] for r in rows if r["market_id"]],
                ledger,
                benchmark["market_id"] if benchmark else None,
            ),
            "note": comparison_note(bool(benchmark), request.include_ttm_valuation),
        }
        if request.include_ttm_valuation:
            result["include_ttm_valuation"] = True
        if benchmark:
            result["benchmark"] = benchmark
        tools = self.tools(request.as_of)
        tools.ledger = ledger
        validate_comparison(result, ledger, tools.domain, request.as_of)
        # Up to eight source-bound claims keep existing v1 Submission limits intact.
        # Every additional table cell and chart observation is independently replayed
        # by the comparison audit, rather than stamped verified by this producer.
        ids = [
            key
            for r in rows
            for key in (
                (r["market_id"],)
                if question and not request.include_ttm_valuation
                else (
                    r["market_id"],
                    r["cells"].get("provider_ttm_pe") or r["cells"].get("provider_ttm_eps"),
                )
                if request.include_ttm_valuation
                else (r["market_id"], r["cells"].get("revenue"))
            )
            if key
        ]
        period_label = {"1Y": "一年", "5Y": "五年", "MAX": "全部历史"}[request.period]
        question = question or (
            "、".join(request.tickers)
            + (
                f" 股票对比：{period_label}走势与供应商 TTM EPS / 市盈率"
                if request.include_ttm_valuation
                else f" 股票对比：年度营收与{period_label}走势"
            )
            + (f"；{request.benchmark} ETF 基准" if benchmark else "")
        )
        claims, findings = verify_submission(
            Submission(claims=[Claim(kind=ledger[key]["kind"], evidence_ids=[key]) for key in ids]),
            tools,
            question,
        )
        gaps = list(
            dict.fromkeys(
                [
                    issue["message"]
                    for r in rows + ([benchmark] if benchmark else [])
                    for issue in r["issues"]
                ]
                + findings
            )
        )
        answer = ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=question,
            as_of=request.as_of,
            mode="comparison",
            status="partial" if gaps or not result["chart"]["found"] else "answered",
            answer=claims,
            gaps=gaps,
            evidence=list(ledger.values()),
            trace=[
                {"stage": "company_comparison", "result": result},
                {
                    "stage": "comparison_sources",
                    "companies": [
                        {"ticker": row["ticker"], "preparation": prepared}
                        for row, _, prepared in observations
                    ],
                },
            ],
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return answer, tools.domain

    def enrich_cached_facts(self, ticker: str) -> None:
        with self.domain_lock:
            if ticker in self.enriched or ticker not in self.domain.companies:
                return
            company = self.domain.companies[ticker]
            if ticker in self.domain.financial_profiles:
                from .ifrs_sources import extract_ifrs_facts
                from .statements import extract_inline_statement_facts

                complete = True
                processed = False
                for entry in self.corpus.catalog(date.today(), ticker):
                    filing = Filing.model_validate(entry)
                    if filing.form not in ("20-F", "20-F/A"):
                        continue
                    html_path = (
                        self.settings.home
                        / "sec-cache"
                        / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")
                    )
                    if not html_path.is_file():
                        complete = False
                        continue
                    html = html_path.read_bytes()
                    if hashlib.sha256(html).hexdigest() != filing.content_sha256:
                        raise ValueError("Cached filing bytes differ from the bound source")
                    self.corpus.add_facts(filing, extract_ifrs_facts(html, filing, self.domain))
                    self.corpus.add_statement_facts(filing, extract_inline_statement_facts(html, filing))
                    processed = True
                if complete and processed:
                    self.enriched.add(ticker)
                return
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json"
            path = (
                self.settings.home
                / "sec-cache"
                / (hashlib.sha256(url.encode()).hexdigest() + ".cache")
            )
            if not path.is_file():
                return
            content = json.loads(path.read_bytes())
            if str(content.get("cik", "")).zfill(10) != company.cik:
                raise ValueError("Cached SEC identity does not match the company")
            for entry in self.corpus.catalog(date.today(), ticker):
                filing = Filing.model_validate(entry)
                if not filing.form.startswith(("10-K", "10-Q")):
                    continue
                new = [
                    f
                    for f in extract_facts(
                        content,
                        filing,
                        self.domain,
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        if filing.form.startswith("10-Q")
                        else None,
                    )
                    if self.corpus.evidence(f.id, date.today()) is None
                ]
                html_path = (
                    self.settings.home
                    / "sec-cache"
                    / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")
                )
                if html_path.is_file():
                    html = html_path.read_bytes()
                    if hashlib.sha256(html).hexdigest() != filing.content_sha256:
                        raise ValueError("Cached filing bytes differ from the bound source")
                    present = {f.id for f in new}
                    new.extend(
                        f
                        for f in extract_cash_facts(html, filing, self.domain)
                        if f.id not in present and self.corpus.evidence(f.id, date.today()) is None
                    )
                self.corpus.add_facts(filing, new)
            self.enriched.add(ticker)

    def peer_candidates(self, ticker: str) -> dict:
        """Return only locally registered candidate peers; no source or model call."""

        self.refresh_domain()
        return candidate_peers(ticker, set(self.domain.companies))

    def historical_valuation(self, ticker: str, period: str = "5Y") -> ResearchAnswer:
        """Return a source-bound historical annual P/E reference package."""

        started = time.perf_counter()
        tools = self.tools(date.today())
        result = tools.get_historical_valuation(ticker, period)
        market_id = result["evidence_id"]
        claims, errors = verify_submission(
            Submission(claims=[Claim(kind="market", evidence_ids=[market_id])]),
            tools,
            f"{ticker} 历史市盈率区间",
            focus_ticker=ticker,
        )
        if not result["found"]:
            errors.append("当前历史日线与公开年度 EPS 没有足够的可对齐有效点；缺口已按原因保留。")
        answer = ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=f"{ticker} 历史市盈率区间",
            as_of=date.today(),
            mode="market",
            status=("partial" if errors else "answered") if claims else "abstained",
            answer=claims,
            gaps=list(dict.fromkeys(errors)),
            evidence=list(tools.ledger.values()),
            trace=[{"stage": "historical_valuation", "result": result["reference"]}],
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            research_type="stock",
        )
        self.corpus.save_run(
            answer.model_dump(mode="json"), create_package(answer, tools.domain, ticker)
        )
        return answer
