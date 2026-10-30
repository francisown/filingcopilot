from __future__ import annotations

import re
import time
import uuid
from dataclasses import replace
from datetime import date

from .agent import ResearchAgent
from .annual_numbers import annual_years
from .cashflow import cashflow_explanation, cashflow_scope, is_cashflow_question
from .comparison import ComparisonRequest, question_benchmark
from .execution import (
    RefreshBudgetExceeded,
    ResearchStopped,
    checkpoint,
    progress,
    run_step,
    stopped,
)
from .fundamentals import STOCK_GAPS, is_stock_question
from .market import question_period
from .models import Claim, ResearchAnswer, ResearchRequest, Submission
from .quarterly import (
    fiscal_quarter_period_end,
    is_quarterly_question,
    requested_fiscal_quarter,
)
from .service_planning import market_failure_gap_message, market_failure_policy_message
from .tools import ResearchTools
from .trends import TrendRequest, trend_years
from .ttm import is_ttm_question
from .updates import is_stock_updates_question, wants_update_summary
from .verification import verify_submission


class ExecutionMixin:
    def _source_needs_preparation(self, request, plan) -> bool:
        """Return whether the requested filing scope is missing locally.

        Agent requests used to enter the model with whatever happened to be in
        the local corpus. That made a fresh installation silently answer from
        an incomplete dataset. Check the concrete ticker and any explicit fiscal
        years before research so the discovery layer can acquire the exact SEC
        reports that the question needs.
        """
        ticker = (request.ticker or "").strip().upper()
        if not ticker:
            return False
        domain = self.refresh_domain()
        if ticker not in domain.companies:
            return True
        annual_forms = domain.annual_forms(ticker)
        filings = [
            row
            for row in self.corpus.catalog(request.as_of, ticker)
            if row.get("form") in annual_forms
        ]
        if not filings:
            return True
        years = tuple(annual_years(request.question))
        if years:
            observed_years = {
                int(row["period_end"][:4])
                for row in filings
                if row.get("period_end")
            }
            if any(year not in observed_years for year in years):
                return True
            relevant = [
                row for row in filings
                if row.get("period_end") and int(row["period_end"][:4]) in years
            ]
        else:
            relevant = [
                max(filings, key=lambda row: (row.get("period_end", ""), row.get("filed", ""), row.get("id", "")))
            ]
        # A catalog row is not enough to answer from a traceable filing.  The
        # raw SEC bytes must still be present and match the immutable content
        # hash before retrieval can use the parsed passages.  Test/offline
        # fetchers remain authoritative through the discovery boundary.
        return any(not self.discovery._cached_filing_is_valid(row) for row in relevant)

    def _prepare_research_sources(self, request, plan):
        """Acquire missing annual sources while preserving the existing cache."""
        ticker = (request.ticker or "").strip().upper()
        years = tuple(annual_years(request.question))
        if (
            plan.get("kind") == "filing"
            and years
            and len(years) <= 3
            and not any(year > request.as_of.year for year in years)
        ):
            return run_step(
                "annual_research_preparation",
                "正在获取问题涉及年度的 SEC 年报原文和数字。",
                self.discovery.prepare_annual_facts,
                ticker,
                request.as_of,
                years,
            )
        return run_step(
            "company_preparation",
            "正在检查并获取知悉日期前的 SEC 年报。",
            self.discovery.prepare,
            ticker,
            request.as_of,
        )

    def _research(self, request: ResearchRequest):
        started = time.perf_counter()
        preparation_trace = []
        source_preparation_failed = False
        checkpoint()
        progress("planning", "正在检查问题范围与知悉日期。")
        plan = self.research_plan(request)
        if not plan["ready"]:
            if plan.get("guidance_only"):
                policy_text = market_failure_policy_message(request.language)
                policy_id = "policy-market-source-unavailable"
                answer = ResearchAnswer(
                    id="run-" + uuid.uuid4().hex,
                    question=request.question,
                    as_of=request.as_of,
                    mode=request.mode,
                    status="partial",
                    status_reason="source_unavailable",
                    answer=[
                        {
                            "kind": "narrative",
                            "text": policy_text,
                            "statement": policy_text,
                            "evidence_ids": [policy_id],
                            "verification": "deterministic",
                        }
                    ],
                    gaps=[market_failure_gap_message(request.language)],
                    evidence=[
                        {
                            "id": policy_id,
                            "kind": "system_policy",
                            "ticker": request.ticker or "",
                            "section": "Market-source failure policy",
                            "text": policy_text,
                            "locator": "service_planning.market_failure_policy_message",
                        }
                    ],
                    trace=[
                        {
                            "stage": "guidance",
                            "kind": "market_source_failure_policy",
                            "message": plan["message"],
                        }
                    ],
                    usage={"model_calls": 0, "total_tokens": 0},
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    model=None,
                    research_type="filing",
                )
                return answer, self.refresh_domain(), request.ticker
            raise ValueError(plan["message"] + " 可选问题：" + " / ".join(plan["suggestions"]))
        if request.as_of > date.today():
            raise ValueError("知悉日期不能晚于今天。")
        if request.depth == "full":
            from .company_report import company_report

            answer, domain, focus = company_report(self, request)
            if answer.research_type == "stock" and focus:
                # A full report already has a section-level independent review.
                # A second reverse-direction retrieval can consume the remaining
                # budget after the report is complete and otherwise discard it at
                # the deadline. Keep it best-effort and preserve the report.
                try:
                    self._contrarian_pass(answer, self.tools(request.as_of), focus)
                except ResearchStopped:
                    answer.gaps.append("反证检索未在本轮预算内完成；已保留六部分报告及其核验结果。")
                    answer.trace.append({"stage": "contrarian_pass", "found": False, "stopped": True})
            return answer, domain, focus
        if plan["kind"] == "company_comparison":
            progress("evidence", "正在获取所列公司的行情、TTM 估值和年度资料。")
            answer, domain = self._compare_companies(
                ComparisonRequest(
                    tickers=plan["comparison_tickers"],
                    period=question_period(request.question),
                    as_of=request.as_of,
                    include_ttm_valuation=True,
                    refresh_sources=False,
                ),
                question=request.question,
            )
            if plan.get("peer_selection"):
                answer.trace.insert(
                    0, {"stage": "peer_selection", "result": plan["peer_selection"]}
                )
            return answer, domain, None
        market_question = plan["kind"] == "market"
        valuation_question = plan["kind"] == "valuation"
        provider_valuation = plan["kind"] == "provider_valuation"
        quarterly_question = (
            is_quarterly_question(request.question)
            and not market_question
            and plan["kind"] != "stock"
        )
        cashflow_question = (
            is_cashflow_question(request.question)
            and (not is_stock_question(request.question) or is_ttm_question(request.question))
            and not market_question
            and plan["kind"] != "stock"
        )
        if (
            request.mode == "agent"
            and self.gateway is None
            and not (
                market_question
                or valuation_question
                or provider_valuation
                or quarterly_question
                or cashflow_question
                or plan["kind"] == "ttm"
                or plan["kind"] == "trend"
                or plan["kind"] == "benchmark"
                or plan["kind"] == "historical_valuation"
                or plan["kind"] == "updates"
                or plan.get("annual_numeric_direct")
            )
        ):
            raise ValueError(
                "尚未配置模型。请选择离线证据检索，或配置 FC_API_BASE / FC_API_KEY / FC_MODEL。"
            )
        if request.company and (
            market_question or provider_valuation or plan["kind"] == "benchmark"
        ):
            lookup = run_step(
                "company_lookup",
                "正在匹配股票代码。",
                self.find_stock,
                request.company,
                translate=request.mode == "agent" and plan["kind"] != "benchmark",
            )
            if lookup["match_count"] != 1:
                raise ValueError("请先从股票搜索结果中选择明确的代码。")
            ticker = lookup["candidates"][0]["ticker"]
            if request.ticker and request.ticker.upper() != ticker:
                raise ValueError("公司名称与所选代码不一致。")
            request = request.model_copy(
                update={"ticker": ticker, "company": None, "auto_acquire": False}
            )
        if request.company:
            lookup = run_step(
                "company_lookup",
                "正在核对 SEC 公司身份。",
                self.discovery.search,
                request.company,
                translate=request.mode == "agent",
            )
            if lookup["match_count"] != 1:
                raise ValueError(lookup["message"])
            ticker = lookup["candidates"][0]["ticker"]
            if request.ticker and request.ticker.upper() != ticker:
                raise ValueError("公司名称与所选股票代码不一致，请重新选择。")
            request = request.model_copy(update={"ticker": ticker, "auto_acquire": True})
            preparation_trace.append(
                {
                    "stage": "company_lookup",
                    "result": lookup,
                    "model": (lookup["translation"] or {}).get("model"),
                    "usage": (lookup["translation"] or {}).get("usage", {}),
                }
            )
        # Company resolution can change the instrument's capability. Recheck the
        # concrete ticker before starting disclosure acquisition or research.
        plan = self.research_plan(request)
        if not plan["ready"]:
            raise ValueError(plan["message"] + " 可选问题：" + " / ".join(plan["suggestions"]))
        should_prepare = (
            request.auto_acquire
            or (
                request.mode == "agent"
                and bool(request.ticker)
                and not market_question
                and not plan.get("annual_numeric_direct")
                and plan["kind"] in ("filing", "stock")
                and (plan["kind"] == "filing" or bool(annual_years(request.question)))
                and self._source_needs_preparation(request, plan)
            )
        )
        if should_prepare and not market_question and not plan.get("annual_numeric_direct") and plan["kind"] not in (
            "updates", "provider_valuation", "trend", "benchmark", "historical_valuation", "valuation"
        ):
            if not request.ticker:
                raise ValueError("自动采集需要明确选择一家公司。")
            try:
                prepared = self._prepare_research_sources(request, plan)
            except (ResearchStopped, RefreshBudgetExceeded):
                raise
            except Exception as exc:
                # A failed SEC refresh must not discard already indexed local
                # evidence. Continue into the normal research path with an
                # explicit warning; if no usable evidence exists, the agent
                # will return a scoped source gap instead of fabricating an
                # answer.
                prepared = {
                    "ticker": request.ticker,
                    "checked": False,
                    "warning": "SEC 原文检查未完成；将继续使用已缓存资料，缺失部分会明确列出。",
                    "error_type": type(exc).__name__,
                }
                source_preparation_failed = True
            preparation_trace.append({"stage": "company_preparation", "result": prepared})
        tools = self.tools(request.as_of)
        checkpoint()
        progress("evidence", "正在读取事实与原文，核对期间并计算。")
        if plan["kind"] == "benchmark":
            focus = request.ticker
            if not focus:
                companies = tools.domain.company_scope(request.question)
                focus = next(iter(companies)) if len(companies) == 1 else None
            if not focus:
                raise ValueError("请选择一只证券后查询基准比较。")
            answer, domain = self._compare_companies(
                ComparisonRequest(
                    tickers=[focus],
                    benchmark=question_benchmark(request.question),
                    period=question_period(request.question),
                    as_of=request.as_of,
                    refresh_sources=False,
                ),
                question=request.question,
            )
            return answer, domain, focus
        if plan["kind"] == "trend":
            focus = request.ticker
            if not focus:
                companies = tools.domain.company_scope(request.question)
                focus = next(iter(companies)) if len(companies) == 1 else None
            if not focus:
                raise ValueError("请选择一家公司后查询多年财务趋势。")
            answer, domain = self._financial_trend(
                TrendRequest(
                    ticker=focus, years=trend_years(request.question), as_of=request.as_of
                ),
                question=request.question,
            )
            return answer, domain, focus
        if plan["kind"] == "updates":
            focus = request.ticker
            if not focus:
                companies = tools.domain.company_scope(request.question)
                focus = next(iter(companies)) if len(companies) == 1 else None
            if not focus:
                raise ValueError("请选择一家公司后查询公司公告。")
            listing = self.market.directory.listing(focus)
            from .updates import update_forms

            if update_forms(listing):
                prepared = run_step(
                    "updates_preparation",
                    "正在检查近 90 天公司公告与发布附件。",
                    self.discovery.prepare_updates,
                    focus,
                    request.as_of,
                )
                preparation_trace.append({"stage": "updates_preparation", "result": prepared})
            else:
                prepared = {
                    "ticker": focus,
                    "checked": False,
                    "acquired": [],
                    "warnings": [],
                    "reason": "该证券没有 SEC 8-K/6-K 申报来源；继续读取登记的市场公告来源。",
                    "scope": "SEC 公司公告来源未接入。",
                }
            if listing and (
                listing.external_sources or listing.hkex_stock_code or listing.cninfo_stock_code
            ):
                external = run_step("external_updates_preparation", "正在读取电话会、新闻或交易所公告。",
                                    self.discovery.prepare_external_updates, focus, request.as_of)
                preparation_trace.append({"stage": "external_updates_preparation", "result": external})
                prepared = {**prepared, "warnings": [*prepared["warnings"], *external["warnings"]]}
            tools = self.tools(request.as_of)
            model_allowed = (
                request.mode == "agent"
                and self.gateway is not None
                and (
                    self.capabilities(focus)["update_form"] != "6-K"
                    or wants_update_summary(request.question)
                )
            )
            empty_summary = (
                wants_update_summary(request.question)
                and model_allowed
                and not tools.get_company_updates(focus)["found"]
            )
            if model_allowed and not empty_summary:
                summary = wants_update_summary(request.question)
                agent_settings = (
                    replace(self.settings, max_rounds=1, max_tool_calls=2)
                    if summary
                    else self.settings
                )
                answer = ResearchAgent(agent_settings, self.gateway).run(request, tools)
                if not answer.answer and not stopped():
                    fallback = self.updates_reference(request, tools, focus, prepared)
                    fallback.trace = answer.trace + fallback.trace
                    fallback.usage = answer.usage
                    fallback.review_independence = answer.review_independence
                    fallback.gaps = [
                        "模型摘要未完成，已保留逐字核验的官方公告摘录。",
                        *fallback.gaps,
                    ]
                    fallback.status = "partial" if fallback.answer else "abstained"
                    answer = fallback
                else:
                    answer.gaps.extend(prepared["warnings"])
                    if answer.gaps:
                        answer.status = "partial"
                if summary and answer.answer:
                    from .updates import summary_events, update_identity

                    overview = next(
                        t["result"] for t in answer.trace if t.get("tool") == "get_company_updates"
                    )
                    covered = {
                        tools.ledger[key].get("filing", {}).get("accession") or "external:" + key
                        for claim in answer.answer
                        if claim["kind"] == "narrative"
                        for key in claim["evidence_ids"]
                    }
                    missing = [e for e in summary_events(overview, request.question) if update_identity(e) not in covered]
                    if missing and any(c["kind"] == "narrative" for c in answer.answer):
                        fallback = self.updates_reference(request, tools, focus, prepared)
                        for claim in fallback.answer:
                            if (
                                (tools.ledger[claim["evidence_ids"][0]].get("filing", {}).get("accession") or "external:" + claim["evidence_ids"][0])
                                not in covered
                            ):
                                claim["index"] = max(c["index"] for c in answer.answer) + 1
                                answer.answer.append(claim)
                        answer.evidence = list(tools.ledger.values())
                        answer.trace.append(
                            {
                                "stage": "notice_summary_fallback",
                                "untranslated_events": len(missing),
                            }
                        )
                        answer.gaps.append(
                            f"{len(missing)} 份公告未完成中文摘要；已保留可读原文与公告链接，可重试或打开原文核对。"
                        )
                        answer.status = "partial"
            else:
                answer = self.updates_reference(request, tools, focus, prepared)
                if wants_update_summary(request.question):
                    answer.gaps.insert(
                        0,
                        "未取得可读公告正文，本次未调用摘要模型；可从公告清单打开原始文件，或调整知悉日期。"
                        if empty_summary
                        else "中文摘要需要已配置模型的 Agent 模式；本次保留逐字核验的官方公告原文。",
                    )
                    if answer.answer:
                        answer.status = "partial"
        elif plan["kind"] == "ttm":
            focus = request.ticker
            if not focus:
                companies = tools.domain.company_scope(request.question)
                focus = next(iter(companies)) if len(companies) == 1 else None
            if not focus:
                raise ValueError("请选择一家公司后查询 TTM 财务流量。")
            annual = (
                run_step(
                    "annual_refresh",
                    "正在检查最新年度披露。",
                    self.discovery.refresh_annual,
                    focus,
                    request.as_of,
                )
                if self.settings.sec_user_agent
                else None
            )
            prepared = run_step(
                "quarterly_preparation",
                "正在检查最新季报与披露日期。",
                self.discovery.prepare_quarterly,
                focus,
                request.as_of,
            )
            if annual:
                preparation_trace.append({"stage": "annual_refresh", "result": annual})
            preparation_trace.append({"stage": "quarterly_preparation", "result": prepared})
            answer = self.ttm_reference(
                request,
                tools,
                focus,
                [r["warning"] for r in (annual, prepared) if r and r.get("warning")],
            )
        elif cashflow_question:
            focus = request.ticker
            if not focus:
                companies = tools.domain.company_scope(request.question)
                focus = next(iter(companies)) if len(companies) == 1 else None
            if not focus:
                raise ValueError("请选择一家公司后查询自由现金流。")
            scope, year = cashflow_scope(request.question)
            annual_prepared = (
                run_step(
                    "annual_refresh",
                    "正在检查最新年度披露。",
                    self.discovery.refresh_annual,
                    focus,
                    request.as_of,
                )
                if self.settings.sec_user_agent
                else None
            )
            if annual_prepared:
                preparation_trace.append({"stage": "annual_refresh", "result": annual_prepared})
            if scope != "annual" and focus not in tools.domain.financial_profiles:
                prepared = run_step(
                    "quarterly_preparation",
                    "正在检查最新季报与披露日期。",
                    self.discovery.prepare_quarterly,
                    focus,
                    request.as_of,
                )
                preparation_trace.append({"stage": "quarterly_preparation", "result": prepared})
            else:
                prepared = {"warning": None}
            if (
                request.mode == "agent"
                and self.gateway is not None
                and cashflow_explanation(request.question)
            ):
                answer = ResearchAgent(self.settings, self.gateway).run(request, tools)
                if prepared.get("warning"):
                    answer.gaps.append(prepared["warning"])
                    if answer.answer:
                        answer.status = "partial"
            else:
                answer = self.cashflow_reference(
                    request, tools, focus, scope, year, prepared.get("warning")
                )
            if annual_prepared and annual_prepared.get("warning"):
                answer.gaps.append(annual_prepared["warning"])
                if answer.answer:
                    answer.status = "partial"
        elif quarterly_question:
            ticker = request.ticker
            if not ticker:
                companies = tools.domain.company_scope(request.question)
                if len(companies) == 1:
                    ticker = next(iter(companies))
            if not ticker:
                raise ValueError("请选择一家公司后查询最新季报。")
            prepared = run_step(
                "quarterly_preparation",
                "正在检查最新季报与披露日期。",
                self.discovery.prepare_quarterly,
                ticker,
                request.as_of,
            )
            preparation_trace.append({"stage": "quarterly_preparation", "result": prepared})
            historical_target = requested_fiscal_quarter(request.question)
            historical_end = (
                fiscal_quarter_period_end(tools, ticker, *historical_target)
                if historical_target
                else None
            )
            if historical_target and historical_end is None:
                answer = self.quarterly_reference(
                    request,
                    tools,
                    ticker,
                    prepared.get("warning"),
                    period_end="9999-12-31",
                )
                answer.gaps.insert(
                    0,
                    f"未找到 FY{historical_target[0]} Q{historical_target[1]} 对应的三份可定位季度序列；系统没有用最新季度替代。",
                )
                answer.status = "partial" if answer.answer else "abstained"
            elif historical_end:
                answer = self.quarterly_reference(
                    request,
                    tools,
                    ticker,
                    prepared.get("warning"),
                    period_end=historical_end,
                )
            else:
                answer = None
            explanatory = bool(
                re.search(
                    r"为什么|为何|原因|风险|解释|驱动|why|risk|explain|driver",
                    request.question,
                    re.I,
                )
            ) or is_stock_question(request.question)
            if answer is not None:
                pass
            elif (
                explanatory
                and request.mode == "agent"
                and self.gateway is not None
                and prepared["found"]
            ):
                answer = ResearchAgent(self.settings, self.gateway).run(request, tools)
                if prepared.get("warning"):
                    answer.gaps.append(prepared["warning"])
                    if answer.answer:
                        answer.status = "partial"
            else:
                answer = self.quarterly_reference(request, tools, ticker, prepared.get("warning"))
        elif plan["kind"] == "historical_valuation":
            ticker = request.ticker
            if not ticker:
                companies = tools.domain.company_scope(request.question)
                if len(companies) == 1:
                    ticker = next(iter(companies))
            if not ticker:
                raise ValueError("请选择一家公司后查询历史市盈率区间。")
            result = run_step(
                "historical_valuation",
                "正在按公开日期对齐历史日线与年度 EPS。",
                tools.get_historical_valuation,
                ticker,
                question_period(request.question),
            )
            market_id = result["evidence_id"]
            direct_claims = [Claim(kind="market", evidence_ids=[market_id])]
            claims, errors = verify_submission(
                Submission(claims=direct_claims),
                tools,
                request.question,
                focus_ticker=ticker,
            )
            if not result["found"]:
                errors.append(
                    "当前历史日线与公开年度 EPS 没有足够的可对齐有效点；缺口已按原因保留。"
                )
            answer = ResearchAnswer(
                id="run-" + uuid.uuid4().hex,
                question=request.question,
                as_of=request.as_of,
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
            return answer, tools.domain, ticker
        elif market_question or valuation_question or provider_valuation:
            ticker = request.ticker
            if not ticker:
                companies = tools.domain.company_scope(request.question)
                if len(companies) == 1:
                    ticker = next(iter(companies))
            if not ticker:
                raise ValueError("请选择一只股票后查询价格或走势图。")
            if provider_valuation:
                include_pe = bool(re.search(r"市盈率|\bp/?e\b", request.question, re.I))
                result = run_step(
                    "valuation",
                    "正在读取供应商 TTM 每股收益与报价。",
                    tools.get_provider_ttm_valuation,
                    ticker,
                    include_pe,
                )
                direct_claims = [
                    Claim(kind=e["kind"], evidence_ids=[i])
                    for i, e in tools.ledger.items()
                    if e["kind"] == "valuation"
                ]
                if result["found"] and include_pe:
                    direct_claims.append(
                        Claim(kind="calculation", evidence_ids=[result["evidence"]["id"]])
                    )
                if not direct_claims:
                    direct_claims = [
                        Claim(kind="market", evidence_ids=[i])
                        for i, e in tools.ledger.items()
                        if e["kind"] == "market"
                    ]
            elif valuation_question:
                years = sorted(
                    {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", request.question)}
                )
                if len(years) > 1:
                    raise ValueError("请一次选择一个 EPS 财年查询年度市盈率。")
                result = run_step(
                    "valuation",
                    "正在读取报价与年度 EPS 并计算估值。",
                    tools.get_stock_valuation,
                    ticker,
                    years[0] if years else None,
                )
                if result.get("source_preparation"):
                    preparation_trace.append(
                        {"stage": "annual_refresh", "result": result["source_preparation"]}
                    )
                direct_claims = (
                    [Claim(kind="calculation", evidence_ids=[result["evidence"]["id"]])]
                    if result["found"]
                    else [
                        Claim(kind="market", evidence_ids=[i])
                        for i, e in tools.ledger.items()
                        if e["kind"] == "market"
                    ]
                )
            else:
                result = run_step(
                    "market",
                    "正在读取行情、币种与历史价格。",
                    tools.get_stock_market,
                    ticker,
                    question_period(request.question),
                )
                direct_claims = [Claim(kind="market", evidence_ids=[result["evidence_id"]])]
            claims, errors = verify_submission(
                Submission(claims=direct_claims),
                tools,
                request.question,
                focus_ticker=ticker,
            )
            if not result["found"]:
                errors.append(result["reason"])
            if result.get("warning") and not any(result["warning"] in error for error in errors):
                errors.append(result["warning"])
            answer = ResearchAnswer(
                id="run-" + uuid.uuid4().hex,
                question=request.question,
                as_of=request.as_of,
                mode="market",
                status=("partial" if errors else "answered") if claims else "abstained",
                answer=claims,
                gaps=errors,
                evidence=list(tools.ledger.values()),
                trace=[
                    {
                        "stage": "tool",
                        "tool": "get_provider_ttm_valuation"
                        if provider_valuation
                        else "get_stock_valuation"
                        if valuation_question
                        else "get_stock_market",
                        "result": result,
                    }
                ],
                usage={"model_calls": 0, "total_tokens": 0},
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                research_type="stock",
            )
        elif plan.get("annual_numeric_direct"):
            answer, tools = self._annual_numeric_answer(request, tools)
        elif request.mode == "agent":
            if is_stock_question(request.question) and (
                not re.search(r"20\d{2}", request.question)
                or is_stock_updates_question(request.question)
            ):
                focus = request.ticker
                if not focus:
                    companies = tools.domain.company_scope(request.question)
                    focus = next(iter(companies)) if len(companies) == 1 else None
                if focus and self.settings.sec_user_agent and not source_preparation_failed:
                    if not re.search(r"20\d{2}", request.question):
                        annual_prepared = run_step(
                            "annual_refresh",
                            "正在检查最新年度披露。",
                            self.discovery.refresh_annual,
                            focus,
                            request.as_of,
                        )
                        preparation_trace.append(
                            {"stage": "annual_refresh", "result": annual_prepared}
                        )
                        prepared = run_step(
                            "quarterly_preparation",
                            "正在检查最新季报与披露日期。",
                            self.discovery.prepare_quarterly,
                            focus,
                            request.as_of,
                        )
                        preparation_trace.append(
                            {"stage": "quarterly_preparation", "result": prepared}
                        )
                    updates = run_step(
                        "updates_preparation",
                        "正在检查近 90 天公司公告与发布附件。",
                        self.discovery.prepare_updates,
                        focus,
                        request.as_of,
                    )
                    preparation_trace.append({"stage": "updates_preparation", "result": updates})
                    listing = self.market.directory.listing(focus)
                    if listing and (
                        listing.external_sources
                        or listing.hkex_stock_code
                        or listing.cninfo_stock_code
                    ):
                        external = run_step(
                            "external_updates_preparation",
                            "正在读取电话会、新闻或交易所公告。",
                            self.discovery.prepare_external_updates,
                            focus,
                            request.as_of,
                        )
                        preparation_trace.append(
                            {"stage": "external_updates_preparation", "result": external}
                        )
                elif focus and self.settings.sec_user_agent and source_preparation_failed:
                    warning = "本轮已记录 SEC 原文准备失败，跳过重复刷新；继续使用可用缓存。"
                    for stage in ("annual_refresh", "quarterly_preparation", "updates_preparation"):
                        preparation_trace.append(
                            {
                                "stage": stage,
                                "result": {"ticker": focus, "checked": False, "warnings": [warning]},
                            }
                        )
            tools.refresh_status = [
                {
                    "stage": entry["stage"],
                    "ticker": entry["result"].get("ticker"),
                    "checked": entry["result"].get("checked", not entry["result"].get("warning")),
                    "warnings": entry["result"].get("warnings", [])
                    + ([entry["result"]["warning"]] if entry["result"].get("warning") else []),
                }
                for entry in preparation_trace
                if entry["stage"]
                in ("annual_refresh", "quarterly_preparation", "updates_preparation", "external_updates_preparation")
            ]
            self._publish_cached_preview(request)
            answer = ResearchAgent(self.settings, self.gateway).run(request, tools)
            for entry in preparation_trace:
                if entry["stage"] == "company_preparation" and entry["result"].get("warning"):
                    answer.gaps.append(entry["result"]["warning"])
                    if answer.answer:
                        answer.status = "partial"
                if entry["stage"] == "updates_preparation":
                    answer.gaps.extend(entry["result"].get("warnings", []))
                    if answer.gaps and answer.answer:
                        answer.status = "partial"
                if entry["stage"] in ("quarterly_preparation", "annual_refresh") and entry[
                    "result"
                ].get("warning"):
                    answer.gaps.append(entry["result"]["warning"])
                    if answer.answer:
                        answer.status = "partial"
        else:
            answer = self.reference(request, tools)
            if is_stock_question(request.question):
                answer.research_type = "stock"
                answer.gaps = list(
                    dict.fromkeys(
                        [
                            *answer.gaps,
                            "当前为离线证据检索；请切换 Agent 研究生成有依据的经营判断和投资条件。",
                            *STOCK_GAPS,
                        ]
                    )
                )
                if answer.answer:
                    answer.status = "partial"
        contrarian_focus = request.ticker
        if not contrarian_focus:
            scopes = tools.domain.company_scope(request.question)
            contrarian_focus = next(iter(scopes)) if len(scopes) == 1 else None
        if answer.research_type == "stock" and contrarian_focus and plan["kind"] == "stock":
            self._contrarian_pass(answer, tools, contrarian_focus)
        if preparation_trace:
            for entry in preparation_trace:
                if entry.get("model"):
                    answer.usage["model_calls"] = answer.usage.get("model_calls", 0) + 1
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        answer.usage[key] = answer.usage.get(key, 0) + int(
                            entry.get("usage", {}).get(key) or 0
                        )
            answer.trace = preparation_trace + answer.trace
            answer.elapsed_ms = round((time.perf_counter() - started) * 1000)
        return answer, tools.domain, request.ticker

    def _contrarian_pass(self, answer: ResearchAnswer, tools: ResearchTools, ticker: str) -> None:
        """Run one deterministic reverse-direction retrieval before delivery.

        Inspired by TradingAgents' public debate idea, this implementation uses
        one deterministic retrieval rather than its full multi-role debate.
        It creates a
        reproducible query from the delivered direction, then exposes only exact
        passage excerpts that pass the ordinary source gate.  It cannot turn a
        missing passage into a negative conclusion.
        """
        text = " ".join(str(item.get("text", "")) for item in answer.answer)
        positive = bool(re.search(r"增长|上升|改善|强劲|增加|增长动力|increase|growth|improv|strong", text, re.I))
        negative = bool(re.search(r"下降|下滑|恶化|风险|压力|减值|减少|decline|weak|risk|impair|pressure", text, re.I))
        reverse_terms = (
            "下滑 下降 风险 压力 减值 竞争恶化 demand weakness decline impairment risk"
            if positive and not negative
            else "增长 上升 改善 需求韧性 increase growth recovery strength resilience"
            if negative and not positive
            else "风险 不确定性 下行 压力 weakness downside uncertainty risk"
        )
        reverse_pattern = (
            r"下滑|下降|风险|压力|减值|竞争恶化|weakness|decline|impairment|risk"
            if positive and not negative
            else r"增长|上升|改善|需求韧性|increase|growth|recovery|strength|resilience"
            if negative and not positive
            else r"风险|不确定性|下行|压力|weakness|downside|uncertainty|risk"
        )
        try:
            result = tools.search_passages(reverse_terms, ticker=ticker, k=5)
        except (ValueError, OSError, RuntimeError) as exc:
            answer.contrarian = {
                "status": "not_found",
                "query": reverse_terms,
                "items": [],
                "note": f"本轮未检索到反证（检索未完成：{type(exc).__name__}）。",
            }
            answer.trace.append({"stage": "contrarian_pass", "query": reverse_terms, "found": False})
            return
        hits = result.get("hits", [])
        reverse_words = re.compile(reverse_pattern, re.I)
        claims = []
        for hit in hits:
            passage = hit.get("passage", {})
            if passage.get("ticker") != ticker.upper() or not reverse_words.search(passage.get("text", "")):
                continue
            claims.append(
                Claim(kind="excerpt", evidence_ids=[passage["id"]], statement=passage["text"])
            )
            if len(claims) >= 3:
                break
        verified, errors = verify_submission(
            Submission(claims=claims),
            tools,
            "反证检索：" + answer.question,
            focus_ticker=ticker,
            allow_excerpts=True,
        )
        items = [
            {
                "title": "反证",
                "text": item["text"],
                "evidence_ids": item["evidence_ids"],
                "verification": item["verification"],
            }
            for item in verified
        ]
        for item in verified:
            evidence_id = item["evidence_ids"][0]
            evidence = tools.ledger.get(evidence_id)
            if evidence and not any(existing.get("id") == evidence_id for existing in answer.evidence):
                answer.evidence.append(evidence)
        answer.contrarian = {
            "status": "found" if items else "not_found",
            "query": reverse_terms,
            "items": items,
            "note": "已检索到并通过原文摘录核验。" if items else "本轮未检索到反证。",
            "errors": errors[:3],
        }
        answer.trace.append(
            {
                "stage": "contrarian_pass",
                "query": reverse_terms,
                "found": bool(items),
                "evidence_count": len(items),
            }
        )
