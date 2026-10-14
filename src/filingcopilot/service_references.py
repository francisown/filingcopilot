from __future__ import annotations

import re
import time
import uuid

from .annual_numbers import annual_years, reference_answer
from .cashflow import cashflow_explanation
from .execution import run_step
from .models import Claim, ResearchAnswer, ResearchRequest, Submission
from .tools import ResearchTools
from .ttm import ADDITIVE_METRICS
from .verification import verify_submission


class ReferencesMixin:
    def updates_reference(self, request, tools, ticker, prepared):
        from .updates import summary_events

        started = time.perf_counter()
        overview = tools.get_company_updates(ticker)
        claims = [
            Claim(kind="excerpt", evidence_ids=[key], statement=tools.ledger[key]["text"])
            for event in summary_events(overview, request.question, limit=10)
            for key in event["evidence_ids"]
        ]
        verified, errors = verify_submission(
            Submission(claims=claims),
            tools,
            request.question,
            focus_ticker=ticker,
            allow_excerpts=True,
        )
        gaps = [*prepared["warnings"], *errors]
        unreadable = sum(not event["evidence_ids"] for event in overview["events"])
        if verified and unreadable:
            gaps.append(f"{unreadable} 份公告未取得可读正文摘录，请打开清单中的原始文件核对。")
        if not verified:
            gaps.append(
                "近 90 天窗口内未取得可读的公告原文。可打开 SEC 公司申报列表或调整知悉日期；已有年度原文可继续查询。"
            )
        return ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=request.question,
            as_of=request.as_of,
            mode="updates",
            status=("partial" if gaps else "answered") if verified else "abstained",
            answer=verified,
            gaps=gaps,
            evidence=list(tools.ledger.values()),
            trace=[{"stage": "tool", "tool": "get_company_updates", "result": overview}],
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    def ttm_reference(self, request, tools, ticker, warnings):
        started = time.perf_counter()
        metrics, ratios = tools.domain.requests(request.question)
        metrics = sorted(metrics & set(ADDITIVE_METRICS)) or (
            []
            if ratios
            else [
                "revenue",
                "net_income",
                "operating_cash_flow",
            ]
        )
        claims, trace, gaps = [], [], list(warnings)
        for metric in metrics:
            result = tools.get_ttm_financial_fact(ticker, metric)
            trace.append({"stage": "tool", "tool": "get_ttm_financial_fact", "result": result})
            gaps.extend(result.get("warnings", []))
            if result["found"]:
                claims.append(Claim(kind="calculation", evidence_ids=[result["evidence"]["id"]]))
            else:
                gaps.append(result["reason"])
        for ratio in sorted(ratios):
            result = tools.get_ttm_margin(ticker, ratio)
            trace.append({"stage": "tool", "tool": "get_ttm_margin", "result": result})
            gaps.extend(result.get("warnings", []))
            if result["found"]:
                claims.append(Claim(kind="calculation", evidence_ids=[result["evidence"]["id"]]))
            else:
                gaps.append(result["reason"])
        if cashflow_explanation(request.question):
            gaps.append(
                "本次 TTM 流量查询提供事实与计算；可用最新季度 Agent 研究追问原因，解释需保留原文期间。"
            )
        if "同比" in request.question:
            gaps.append(
                "TTM 同比还需更早的年报及累计比较期间；本次展示当期 TTM，不用累计同比替代。"
            )
        accepted, errors = verify_submission(
            Submission(claims=claims, gaps=gaps[:8]), tools, request.question, focus_ticker=ticker
        )
        return ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=request.question,
            as_of=request.as_of,
            mode="ttm",
            status=("partial" if gaps or errors else "answered") if accepted else "abstained",
            answer=accepted,
            gaps=list(dict.fromkeys([*gaps, *errors])),
            evidence=list(tools.ledger.values()),
            trace=trace,
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    def cashflow_reference(
        self, request, tools, ticker, period="latest", fiscal_year=None, warning=None
    ):
        started = time.perf_counter()
        result = tools.get_cashflow(ticker, period, fiscal_year)
        gaps = [*result.get("warnings", []), *([warning] if warning else [])]
        if cashflow_explanation(request.question):
            gaps.append(
                "本次离线查询提供现金流事实及计算；原因解释请使用 Agent 研究，并核对所引申报原文。"
            )
        claims = []
        if result["found"]:
            current = tools.ledger[result["current_id"]]
            claims = [Claim(kind="calculation", evidence_ids=[current["id"]])]
            if period == "ttm":
                claims += [
                    Claim(kind="calculation", evidence_ids=[i])
                    for i in result.get("component_ids", [])
                ]
                if "同比" in request.question:
                    gaps.append("TTM 同比需要更早的年报及累计比较期间；本次不以累计同比替代。")
            else:
                claims += [Claim(kind="fact", evidence_ids=[e["id"]]) for e in current["inputs"]]
            for key in ("change_id", "growth_id"):
                if result.get(key):
                    claims.append(Claim(kind="calculation", evidence_ids=[result[key]]))
            requested_metrics, _ = tools.domain.requests(request.question)
            basis = result.get("capex_basis", {})
            if (
                "cash_capex" in requested_metrics
                or any(word in request.question for word in ("净购置", "毛额", "净额"))
                or re.search(r"net (?:cash )?PP&E purchases", request.question, re.I)
            ):
                if basis.get("gross_id"):
                    evidence = tools.ledger[basis["gross_id"]]
                    claims.append(Claim(kind=evidence["kind"], evidence_ids=[evidence["id"]]))
                for key in ("net_id", "net_growth_id"):
                    if basis.get(key):
                        claims.append(Claim(kind="calculation", evidence_ids=[basis[key]]))
                if not basis.get("net_id") and any(
                    word in request.question.lower() for word in ("净", "net")
                ):
                    gaps.append(
                        "未取得同期间且已审核口径的出售及激励收款，购置净额尚未计算；请核对所引现金流量表及公司净资本开支定义，不能把毛额或缺值当作净额。"
                    )
            for evidence_id in result.get("component_growth_ids", []):
                if tools.ledger[evidence_id]["inputs"][0]["metric"] in requested_metrics:
                    claims.append(Claim(kind="calculation", evidence_ids=[evidence_id]))
        else:
            gaps.append(result["reason"])
        claims = list({(c.kind, tuple(c.evidence_ids)): c for c in claims}.values())
        accepted, errors = verify_submission(
            Submission(claims=claims, gaps=gaps[:8]), tools, request.question, focus_ticker=ticker
        )
        gaps = list(dict.fromkeys([*gaps, *errors]))
        return ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=request.question,
            as_of=request.as_of,
            mode="cashflow",
            status=("partial" if gaps else "answered") if accepted else "abstained",
            answer=accepted,
            gaps=gaps,
            evidence=list(tools.ledger.values()),
            trace=[{"stage": "tool", "tool": "get_cashflow", "result": result}],
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    def quarterly_reference(self, request, tools, ticker, warning=None, period_end=None):
        """A useful zero-model latest-quarter query with explicit source periods."""
        started = time.perf_counter()
        cumulative = bool(re.search(r"半年|累计|YTD", request.question, re.I))
        metrics, ratios = self.domain.requests(request.question)
        needed = self.domain.allowed_metrics(request.question)
        if not ratios and re.search(r"利润率|profit margin", request.question, re.I):
            margins = {
                key for key, ratio in self.domain.ratios.items() if ratio.denominator == "revenue"
            }
            ratios = {
                key for key in margins if self.domain.ratios[key].numerator in metrics
            } or margins
            needed.update(
                metric
                for key in ratios
                for metric in (
                    self.domain.ratios[key].numerator,
                    self.domain.ratios[key].denominator,
                )
            )
        if not needed:
            needed = {
                "revenue",
                "operating_income",
                "net_income",
                "diluted_eps",
                "operating_cash_flow",
            }
        from .quarterly import quarterly_overview

        overview = quarterly_overview(
            tools,
            ticker,
            "ytd" if cumulative else "quarter",
            "ytd" if cumulative or not metrics else "quarter",
            needed,
            period_end=period_end,
        )
        growth = not metrics or bool(
            re.search(r"同比|year.over.year|growth", request.question, re.I)
        )
        claims, gaps = [], list(overview.get("coverage_warnings", []))
        for compact in overview.get("evidence", []):
            item = tools.ledger[compact["id"]]
            inputs = [item] if item["kind"] == "fact" else item["inputs"]
            included = {e["metric"] for e in inputs} <= needed
            if item["kind"] == "calculation" and item["operation"] == "growth" and not growth:
                continue
            if (
                item["kind"] == "calculation"
                and item["operation"] == "ratio"
                and not (ratios or not metrics)
            ):
                continue
            if included:
                claims.append(Claim(kind=item["kind"], evidence_ids=[item["id"]]))
        current_ratios = set()
        for claim in claims:
            item = tools.ledger[claim.evidence_ids[0]]
            if item.get("operation") != "ratio" or any(
                e["end"] != overview["period_end"] for e in item["inputs"]
            ):
                continue
            current_ratios.update(
                key
                for key in ratios
                if [e["metric"] for e in item["inputs"]]
                == [self.domain.ratios[key].numerator, self.domain.ratios[key].denominator]
            )
        for key in sorted(ratios - current_ratios):
            gaps.append(
                f"{self.domain.ratios[key].label}在本次所选报告期间未能计算；请核对同期间利润与正值营收，不能以上年利润率代替。"
            )
        covered = {
            e["metric"]
            for c in claims
            for e in (
                [tools.ledger[c.evidence_ids[0]]]
                if c.kind == "fact"
                else tools.ledger[c.evidence_ids[0]]["inputs"]
            )
        }
        for metric in sorted(needed - covered):
            gaps.append(
                f"{self.domain.metrics[metric].label}在本次 10-Q 中缺少所需期间事实；可查看对应申报原文，不能以年度数代替。"
            )
        if growth:
            grown = {
                tools.ledger[c.evidence_ids[0]]["inputs"][0]["metric"]
                for c in claims
                if c.kind == "calculation"
                and tools.ledger[c.evidence_ids[0]]["operation"] == "growth"
            }
            for metric in sorted((metrics or needed) & covered - grown):
                gaps.append(
                    f"{self.domain.metrics[metric].label}同比缺少可比上年期间或正数基期；本次仅列原始事实。"
                )
        if warning:
            gaps.append(warning)
        if not overview["found"]:
            gaps.extend(overview["missing"])
        if re.search(
            r"为什么|为何|原因|风险|解释|驱动|why|risk|explain|driver", request.question, re.I
        ):
            gaps.append(
                "本次查询提供季报事实与计算。经营原因和风险解释请使用股票研究或在原始 10-Q 中核对管理层讨论。"
            )
        if len(claims) > 10:
            gaps.append("输出已达 10 项上限；可选择营收、利润或现金流缩小范围。")
        accepted, errors = verify_submission(
            Submission(claims=claims[:10]), tools, request.question, focus_ticker=ticker
        )
        gaps = list(dict.fromkeys([*gaps, *errors]))
        return ResearchAnswer(
            id="run-" + uuid.uuid4().hex,
            question=request.question,
            as_of=request.as_of,
            mode="quarterly",
            status=("partial" if gaps else "answered") if accepted else "abstained",
            answer=accepted,
            gaps=gaps,
            evidence=list(tools.ledger.values()),
            trace=[{"stage": "tool", "tool": "get_quarterly_overview", "result": overview}],
            usage={"model_calls": 0, "total_tokens": 0},
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    def _annual_numeric_answer(self, request, tools):
        started = time.perf_counter()
        scope = tools.domain.company_scope(request.question)
        ticker = request.ticker or (next(iter(scope)) if len(scope) == 1 else None)
        years = annual_years(request.question)
        needed = tools.domain.allowed_metrics(request.question)
        if scope and request.ticker and scope != {request.ticker}:
            return self.reference(request, tools), tools
        preparation = None
        missing = not years or ticker not in tools.domain.companies
        if not missing:
            missing = any(
                not tools.get_financial_fact(ticker, metric, year)["found"]
                for metric in needed for year in years
            )
        if (
            ticker and missing and (request.mode == "agent" or request.auto_acquire)
            and not any(year > request.as_of.year for year in years)
        ):
            preparation = run_step(
                "annual_numeric_preparation", "正在获取指定年度的财务来源，无需建立向量索引。",
                self.discovery.prepare_annual_facts, ticker, request.as_of, years or None,
            )
            tools = self.tools(request.as_of)
        answer = self.reference(request, tools)
        answer.trace.insert(0, {"stage": "annual_numeric_path", "requested_years": list(years),
                                "source_preparation": preparation, "model_calls": 0})
        if preparation and preparation.get("warning"):
            answer.gaps.append(preparation["warning"])
            if answer.answer:
                answer.status = "partial"
        answer.elapsed_ms = round((time.perf_counter() - started) * 1000)
        return answer, tools

    def reference(self, request: ResearchRequest, tools: ResearchTools) -> ResearchAnswer:
        return reference_answer(request, tools, self.domain)
