"""Typed annual queries and evidence-rendered answers, without a model round trip."""

import re
import time
import uuid

from .domain import Domain
from .models import Claim, ResearchAnswer, ResearchRequest, Submission
from .tools import ResearchTools
from .verification import verify_submission


def annual_years(question: str) -> tuple[int, ...]:
    years = {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)}
    if any(t in question.lower() for t in ("同比", "growth", "year-over-year")):
        years.update(y - 1 for y in list(years))
    return tuple(sorted(years))


def numeric_annual_question(question: str, domain: Domain) -> bool:
    """Choose code for typed amounts/ratios; leave interpretation to the agent.

    This routes requests, not source verification. Existing claim gates remain
    authoritative. Acquisition can bind at most three annual end-years.
    """
    return bool(domain.allowed_metrics(question)) and len(annual_years(question)) <= 3 and not re.search(
        r"为什么|为何|原因|风险|解释|驱动|分析|评价|来源|商业模式|业务|构成|结构|因素|质量|对应|用于|用途|投向|"
        r"预计|预测|预期|前景|竞争|优势|可持续|是否|差额|差值|最高|最低|排名|哪年|"
        r"why|risk|explain|driver|analys|business|sources?|composition|quality|structure|used for|uses of|purpose|"
        r"how (?:does|did|is|are|can)|forecast|project(?:ed|ion)|expect|outlook|sustain|difference|higher|lower|ranking",
        question, re.I,
    )


def reference_answer(request: ResearchRequest, tools: ResearchTools, domain: Domain) -> ResearchAnswer:
    started = time.perf_counter()
    question = request.question
    companies = domain.company_scope(question)
    if not companies and request.ticker:
        companies = {request.ticker.upper()}
    years = sorted({int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)})
    metrics, ratios = domain.requests(question)
    needed = domain.allowed_metrics(question)
    growth = any(t in question.lower() for t in ("同比", "growth", "year-over-year"))
    gaps, claims, trace = [], [], []

    def call(name, arguments):
        result = tools.call(name, arguments)
        trace.append({"stage": "tool", "tool": name, "arguments": arguments, "result": result})
        return result

    for ticker in sorted(companies):
        filings = [
            f
            for f in tools.list_filings(ticker)["filings"]
            if f["form"] in ("10-K", "10-K/A", "20-F", "20-F/A")
        ]
        scoped_years = years or ([int(filings[0]["period_end"][:4])] if filings else [])
        if not filings:
            gaps.append(f"{ticker} 在知悉日期前没有可用年度申报。")
        observed = {}
        for metric in sorted(needed):
            fetch_years = set(scoped_years)
            if growth and metric in metrics:
                fetch_years.update(year - 1 for year in scoped_years)
            for year in sorted(fetch_years):
                result = call(
                    "get_financial_fact",
                    {"ticker": ticker, "metric": metric, "fiscal_year": year},
                )
                if result["found"]:
                    evidence = result["evidence"]
                    observed[(metric, year)] = evidence
                    if year in scoped_years:
                        claims.append(Claim(kind="fact", evidence_ids=[evidence["id"]]))
                else:
                    label = domain.metrics[metric].label
                    gaps.append(f"{ticker} FY{year} 的{label}在知悉日期前不可用。")
        for year in scoped_years:
            pairs = [
                ("growth", (metric, year), (metric, year - 1))
                for metric in sorted(metrics)
                if growth
            ]
            pairs += [
                (
                    "ratio",
                    (domain.ratios[key].numerator, year),
                    (domain.ratios[key].denominator, year),
                )
                for key in sorted(ratios)
            ]
            for operation, left, right in pairs:
                if left not in observed or right not in observed:
                    continue
                try:
                    result = call(
                        "calculate",
                        {
                            "operation": operation,
                            "left_id": observed[left]["id"],
                            "right_id": observed[right]["id"],
                        },
                    )
                    claims.append(
                        Claim(kind="calculation", evidence_ids=[result["evidence"]["id"]])
                    )
                except ValueError as exc:
                    gaps.append(f"{ticker} FY{year} 计算不可用：{exc}")
    if growth and ratios and not metrics:
        gaps.append("当前同比工具支持同一财务事实的金额增长；利润率的同比变化尚未支持。")
    if not companies and needed:
        gaps.append("请选择或明确写出公司的名称。")
    descriptive = not needed or any(
        t in question.lower()
        for t in ("原因", "为什么", "风险", "解释", "why", "risk", "explain")
    )
    if descriptive:
        for ticker in sorted(companies) or [None]:
            result = call(
                "search_passages",
                {
                    "query": question,
                    "ticker": ticker,
                    "k": 3,
                    "fiscal_year": max(years) if years else None,
                },
            )
            for hit in result["hits"]:
                evidence = tools.ledger[hit["passage"]["id"]]
                claims.append(
                    Claim(
                        kind="excerpt",
                        evidence_ids=[evidence["id"]],
                        statement=evidence["text"],
                    )
                )
        gaps.append("离线模式提供原文摘录，不自动生成管理层解释或因果结论。")
    if len(claims) > 10:
        gaps.append("本次输出达到 10 项上限；请缩小公司、年份或指标范围获取剩余内容。")
        claims = claims[:10]
    gaps = list(dict.fromkeys(gaps))
    if len(gaps) > 8:
        gaps = gaps[:7] + ["还有其他缺失事实；请缩小研究范围。"]
    verified, errors = verify_submission(
        Submission(claims=claims, gaps=gaps),
        tools,
        question,
        focus_ticker=request.ticker,
        allow_excerpts=True,
    )
    trace.append({"stage": "verify", "accepted_claims": len(verified), "findings": errors})
    gaps += errors
    ids = {evidence_id for claim in verified for evidence_id in claim["evidence_ids"]}
    return ResearchAnswer(
        id="run-" + uuid.uuid4().hex,
        question=question,
        as_of=request.as_of,
        mode="reference",
        status=("partial" if gaps else "answered") if verified else "abstained",
        answer=verified,
        gaps=gaps,
        evidence=[tools.ledger[i] for i in sorted(ids)],
        trace=trace,
        usage={"model_calls": 0, "total_tokens": 0},
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
