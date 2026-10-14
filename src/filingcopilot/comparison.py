"""Company comparison built from dated facts and observed daily prices, without a model."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import Field, field_validator, model_validator

from .market import MarketEvidence, symbol
from .models import StrictModel
from .periods import evidence_period
from .valuation import ValuationEvidence, provider_pe

SCHEMA = "filingcopilot.company-comparison/v1"
BENCHMARK_SCHEMA = "filingcopilot.company-comparison/v2"
VALUATION_SCHEMA = "filingcopilot.company-comparison/v3"
BENCHMARKS = {
    "SPY": {
        "name": "标普 500 ETF",
        "currency": "USD",
        "source_url": "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy",
    },
    "QQQ": {
        "name": "纳斯达克 100 ETF",
        "currency": "USD",
        "source_url": "https://www.invesco.com/qqq-etf/en/home.html",
    },
}
NOTE = (
    "走势以共同观察日期的首日为 100，仅保留各证券都有日线的本地日期，不插值；"
    "不同交易所的收盘时点并未同步。区间变化以各自币种计算，未换汇、未年化，"
    "供应商复权序列不等于独立核验的含分红总回报。年度数字按各公司实际财年，"
    "不自动代表同一日历期间；年度 EPS 市盈率不是 TTM 或预测市盈率。"
)
BENCHMARK_NOTE = NOTE + (
    " 基准使用 SPY 或 QQQ 的 ETF 行情，不是指数点位；受基金费用和跟踪差异影响。"
    "相对涨跌差为同一共同日期窗口的区间涨跌相减，单位为百分点，不是年化超额收益或风险调整 alpha。"
)
TTM_NOTE = (
    " 所选公司的供应商 TTM 稀释 EPS 和市盈率单独比较，不属于上方年度财务期间。"
    "各公司期末标签可能不同且按日历季度标准化；未重建 SEC 稀释股数或独立核对供应商方法。"
    "使用同一列报价与同币种 EPS；亏损、过期、拆股口径未匹配时不计算市盈率。"
    "公司由用户选择，不代表自动筛选的同行，倍数高低不能单独判断贵贱。"
)


def comparison_note(benchmark: bool, valuation: bool = False) -> str:
    return (BENCHMARK_NOTE if benchmark else NOTE) + (TTM_NOTE if valuation else "")


class ComparisonRequest(StrictModel):
    tickers: list[str] = Field(min_length=1, max_length=4)
    benchmark: Literal["SPY", "QQQ"] | None = None
    period: Literal["1Y", "5Y", "MAX"] = "5Y"
    as_of: date = Field(default_factory=date.today)
    refresh_prices: bool = False
    refresh_sources: bool = True
    include_ttm_valuation: bool = False
    language: Literal["zh", "en"] = "en"

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values):
        values = [symbol(value) for value in values]
        if len(set(values)) != len(values):
            raise ValueError("请选择 2 至 4 个不同的证券代码。")
        return values

    @field_validator("as_of")
    @classmethod
    def no_future(cls, value):
        if value > date.today():
            raise ValueError("知悉日期不能晚于今天。")
        return value

    @model_validator(mode="after")
    def comparison_scope(self):
        if len(self.tickers) < 2 and not self.benchmark:
            raise ValueError("请选择至少两只证券，或为单只股票选择 SPY / QQQ 基准。")
        if self.benchmark in self.tickers:
            raise ValueError("基准不能同时作为所选证券，请移除重复代码。")
        return self


def question_benchmark(question: str) -> str | None:
    """Only plain historical benchmark requests, not business/causal/mixed questions."""
    import re

    if not re.search(
        r"大盘|基准|标普|纳斯达克|\b(?:SPY|QQQ|S&P|Nasdaq|benchmark)\b", question, re.I
    ):
        return None
    if not re.search(
        r"股价|走势|涨跌|跑赢|跑输|相比|比较|对比|price|perform|compar|outperform|underperform",
        question,
        re.I,
    ):
        return None
    if re.search(
        r"营收|营业|利润|现金流|估值|市盈率|原因|为什么|风险|年报|预测|未来|明天|TTM|revenue|profit|cash.?flow|valuation|why|risk|forecast",
        question,
        re.I,
    ):
        return None
    spy = bool(re.search(r"标普|\b(?:SPY|S&P)\b", question, re.I))
    qqq = bool(re.search(r"纳斯达克|\b(?:QQQ|Nasdaq)\b", question, re.I))
    if spy and qqq or re.search(r"沪深|上证|恒生|罗素|道琼斯|Russell|Dow Jones", question, re.I):
        return None
    return "QQQ" if qqq else "SPY"


def question_ttm_companies(question: str) -> list[str] | None:
    """Read an explicit company list in a plain numeric TTM comparison request.

    Keep unresolved names in the list: the planner must ask for an explicit
    selection rather than silently dropping a company it cannot identify.
    Qualitative and mixed financial requests keep their existing research path.
    """
    import re

    if not re.search(r"每股|EPS|市盈率|\bp/?e\b", question, re.I):
        return None
    if re.search(
        r"为什么|为何|原因|风险|分析|评价|贵|便宜|合理|值得|预测|未来|明天|"
        r"营收|营业|利润|现金流|年报|forward|forecast|why|risk|analys|cheap|expensive|"
        r"revenue|profit|cash.?flow",
        question,
        re.I,
    ):
        return None
    match = re.match(
        r"^\s*(?:(?:请\s*)?(?:比较|对比)\s*|compare\s+)?"
        r"(.+?)\s*(?:的\s*)?(?:供应商\s*)?"
        r"(?:TTM(?![A-Za-z0-9])|滚动(?:十二|12)个月|过去十二个月)",
        question,
        re.I,
    )
    if not match:
        return None
    # Accept numeric fields and an optional chart window, without discarding an
    # additional company, benchmark, forecast or other request after the marker.
    tail = re.sub(
        r"稀释每股收益|每股收益|每股盈利|市盈率|每股|供应商|当前|现在|最新|"
        r"是多少|多少|比较|对比|查询|以及|和|与|及|的|并|近|过去|最近|"
        r"上市以来|全部历史|全部|历史|股价|报价|价格|走势图|走势|涨跌|"
        r"(?:一|三|五|十|1|3|5|10)\s*年|(?:六|6)\s*个?月|"
        r"(?<![A-Za-z0-9_])(?:EPS|P/E|PE)(?![A-Za-z0-9_])|"
        r"\b(?:diluted|earnings|per|share|and|compare|comparison|"
        r"current|supplier|price|prices|chart|history|all|max|over|for|past|last|with|trends?)\b|"
        r"(?:1|3|5|10|one|three|five|ten)[ -]*(?:years?|y)|6\s*(?:months?|m)|[\s，,、;；:：?？/（）()。.]+",
        "",
        question[match.end() :],
        flags=re.I,
    )
    if tail:
        return None
    labels = [
        value.strip()
        for value in re.split(r"[、,，;；&]|和|与|\band\b|\bvs\b\.?", match[1], flags=re.I)
    ]
    return labels if len(labels) >= 2 else None


def rebased_chart(market_ids: list[str], ledger: dict[str, dict]) -> dict:
    """Use one common price basis and the intersection of observed local trading dates."""
    snapshots = [MarketEvidence.model_validate(ledger[key]).snapshot for key in market_ids]
    if len(snapshots) < 2:
        return {"found": False, "reason": "至少两只证券取得日线后才能比较走势。", "series": []}
    if any(s.basis == "split_adjusted_close" for s in snapshots) and not all(
        s.basis == "split_adjusted_close" for s in snapshots
    ):
        return {
            "found": False,
            "reason": "独立供应商拆股调整日线与其他来源口径未对齐；保留单只走势，等待同一来源恢复后再比较。",
            "series": [],
        }
    common = set.intersection(*({p.date for p in s.points} for s in snapshots))
    days = sorted(common)
    if len(days) < 2:
        return {
            "found": False,
            "reason": "共同观察日期不足两个，保留各公司的数据；可改选五年或全部历史后重试。",
            "series": [],
        }
    basis = (
        "split_adjusted_close"
        if all(s.basis == "split_adjusted_close" for s in snapshots)
        else "adjusted_close"
        if all(s.basis == "adjusted_close" for s in snapshots)
        else "close"
    )
    series = []
    for key, snapshot in zip(market_ids, snapshots):
        observed = {p.date: p for p in snapshot.points}
        values = [
            observed[day].adjusted if basis == "adjusted_close" else observed[day].close
            for day in days
        ]
        points = [
            {"date": day.isoformat(), "value": str(value / values[0] * 100), "price": str(value)}
            for day, value in zip(days, values)
        ]
        series.append(
            {
                "ticker": snapshot.ticker,
                "market_id": key,
                "currency": snapshot.currency,
                "change_pct": str((values[-1] / values[0] - 1) * 100),
                "observed_start": snapshot.points[0].date.isoformat(),
                "observed_end": snapshot.points[-1].date.isoformat(),
                "observed_days": len(snapshot.points),
                "omitted_days": len(snapshot.points) - len(days),
                "points": points,
            }
        )
    return {
        "found": True,
        "basis": basis,
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "common_days": len(days),
        "series": series,
        "warning": "部分证券没有完整复权序列，所有曲线统一使用未复权 Close；拆股和分红可能影响比较。"
        if basis == "close"
        else None,
    }


def comparison_chart(market_ids: list[str], ledger: dict, benchmark_id: str | None = None) -> dict:
    chart = rebased_chart(market_ids + ([benchmark_id] if benchmark_id else []), ledger)
    if benchmark_id and chart["found"]:
        reference = chart["series"][-1]
        chart["relative"] = [
            {
                "ticker": s["ticker"],
                "benchmark": reference["ticker"],
                "market_id": s["market_id"],
                "benchmark_market_id": benchmark_id,
                "difference_pp": str(Decimal(s["change_pct"]) - Decimal(reference["change_pct"])),
            }
            for s in chart["series"][:-1]
        ]
    return chart


def validate_market_scope(market, ticker: str, period: str, as_of: date):
    cutoff = min(as_of, market.retrieved_at.astimezone(ZoneInfo(market.exchange_timezone)).date())
    if market.stale:
        cutoff = min(cutoff, market.retrieved_at.date())
    if market.ticker != ticker or market.as_of != cutoff or market.period != period:
        raise ValueError("Comparison market scope mismatch")


def collect_benchmark(tools, ticker: str, period: str, *, refresh_prices=False) -> dict:
    row = {"ticker": ticker, **BENCHMARKS[ticker], "market_id": None, "issues": []}
    try:
        snapshot = tools.market.history(ticker, period, tools.as_of, refresh=refresh_prices)
        if snapshot.instrument_type != "ETF" or snapshot.currency != BENCHMARKS[ticker]["currency"]:
            raise ValueError("Benchmark listing type or currency mismatch")
        from .market import market_evidence

        item = market_evidence(snapshot)
        tools.ledger[item["id"]] = item
        row["market_id"] = item["id"]
        if snapshot.stale:
            row["issues"].append(
                {
                    "scope": "market",
                    "message": "基准使用保存的行情，未确认本次来源已更新；可稍后刷新。",
                }
            )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        row["issues"].append(
            {
                "scope": "market",
                "message": f"{ticker} 基准行情未取得（{type(exc).__name__}）；公司资料仍保留，请刷新基准重试。",
            }
        )
    return row


def collect_company(tools, ticker: str, period: str, *, refresh_prices: bool = False) -> dict:
    """Gather independent cells; unavailable valuation must not hide annual or price data."""
    row = {"ticker": ticker, "market_id": None, "annual_basis_id": None, "cells": {}, "issues": []}
    # Network preparation belongs to the service; this function observes and binds results.
    try:
        snapshot = tools.market.history(ticker, period, tools.as_of, refresh=refresh_prices)
        from .market import market_evidence

        item = market_evidence(snapshot)
        tools.ledger[item["id"]] = item
        row["market_id"] = item["id"]
        if snapshot.stale:
            row["issues"].append(
                {"scope": "market", "message": "使用保存的行情；当前连接未返回新报价，可刷新重试。"}
            )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        row["issues"].append(
            {
                "scope": "market",
                "message": f"行情未取得（{type(exc).__name__}）；可重试获取，年度数字仍可查看。",
            }
        )
    overview = tools.get_company_overview(ticker)
    if not overview["found"]:
        listing = tools.market.directory.listing(ticker)
        unsupported = (
            listing and (not listing.filings_supported or listing.annual_text_only)
        ) or tools.market.filing_scope(ticker) is False
        row["issues"].append(
            {
                "scope": "annual",
                "message": "该证券的年度财务数字尚未接入；已取得的走势仍可比较。可添加已接入 SEC 财务的公司比较经营指标。"
                if unsupported
                else "未取得可用年度财务。可重试自动准备，或先比较走势。",
            }
        )
        return row
    year = overview["fiscal_year"]
    facts = {
        e["metric"]: tools.ledger[e["id"]]
        for e in overview["evidence"]
        if e["kind"] == "fact" and e["fiscal_year"] == year
    }
    basis = facts.get("revenue") or next(iter(facts.values()))
    row["annual_basis_id"] = basis["id"]
    # A column has a single annual period/version. Other observations remain in provenance.
    for metric, fact in facts.items():
        if (fact["start"], fact["end"], fact["filing_id"]) == (
            basis["start"],
            basis["end"],
            basis["filing_id"],
        ):
            row["cells"][metric] = fact["id"]
    for e in overview["evidence"]:
        if e.get("operation") == "growth" and e["fiscal_year"] == year:
            full = tools.ledger[e["id"]]
            metric = full["inputs"][0]["metric"]
            if row["cells"].get(metric) == full["input_ids"][0]:
                row["cells"][metric + "_growth"] = e["id"]
    for metric, key in (("operating_income", "operating_margin"), ("net_income", "net_margin")):
        if metric in row["cells"] and "revenue" in row["cells"]:
            try:
                row["cells"][key] = tools.calculate(
                    "ratio", row["cells"][metric], row["cells"]["revenue"]
                )["evidence"]["id"]
            except ValueError:
                pass
    try:
        cash = tools.get_cashflow(ticker, "annual", year)
        if cash["found"]:
            full = tools.ledger[cash["current_id"]]
            ocf, capex = full["inputs"][:2]
            if row["cells"].get("operating_cash_flow") == ocf["id"]:
                # Use the same gross PP&E definition for every company, including AMZN.
                row["cells"]["cash_capex"] = capex["id"]
                row["cells"]["free_cash_flow"] = tools.calculate(
                    "free_cash_flow", ocf["id"], capex["id"]
                )["evidence"]["id"]
        else:
            row["issues"].append({"scope": "cashflow", "message": cash["reason"]})
    except (ValueError, OSError):
        pass
    if row["market_id"]:
        profile = tools.domain.financial_profiles.get(ticker)
        metric = (
            "ads_diluted_eps" if profile and "ads_diluted_eps" in profile.metrics else "diluted_eps"
        )
        try:
            eps = tools.get_financial_fact(ticker, metric, year)
            if eps["found"] and (eps["evidence"]["start"], eps["evidence"]["end"]) == (
                basis["start"],
                basis["end"],
            ):
                row["cells"]["annual_pe"] = tools.calculate(
                    "annual_pe", row["market_id"], eps["evidence"]["id"]
                )["evidence"]["id"]
        except ValueError:
            pass
    missing = [
        key
        for key in (
            "revenue_growth",
            "operating_margin",
            "net_margin",
            "free_cash_flow",
            "annual_pe",
        )
        if key not in row["cells"]
    ]
    if missing:
        row["issues"].append(
            {
                "scope": "cells",
                "missing": missing,
                "message": "部分指标缺少同期间、同口径的有效输入；可展开已有数字查来源，或重试准备资料。历史日期不生成当前市盈率，零或亏损 EPS 不计算正市盈率。",
            }
        )
    return row


def collect_comparison_valuation(tools, row: dict) -> None:
    """Observe optional supplier EPS separately; bind P/E to this column's quote."""
    ticker = row["ticker"]

    def missing(message, keys=("provider_ttm_eps", "provider_ttm_pe")):
        row["issues"].append({"scope": "valuation", "missing": list(keys), "message": message})

    if tools.as_of != tools.market.now().astimezone().date():
        missing(
            "供应商 TTM 历史发布版本尚未接入；历史走势和已公开年度数字保留。可选今天查询当前 TTM 估值。"
        )
        return
    if ticker in tools.domain.financial_profiles:
        missing(
            "该证券的供应商 TTM 股份类别尚未匹配；可查看已接入的 SEC 年度 EPS 估值，不替换为普通股或其他市场的 EPS。"
        )
        return
    market = tools.ledger.get(row["market_id"])
    if not market or market["snapshot"]["instrument_type"] != "EQUITY":
        missing(
            "本列未取得有效股票报价；可刷新行情后再对比 TTM 估值。基金等产品不套用股票 EPS 市盈率。"
        )
        return
    try:
        observed = tools.get_provider_ttm_valuation(ticker, include_pe=False)
        if not observed["found"]:
            missing(observed["reason"])
            return
        eps = observed["evidence"]
        row["cells"]["provider_ttm_eps"] = eps["id"]
        if observed.get("warning"):
            row["issues"].append({"scope": "valuation", "message": observed["warning"]})
        try:
            row["cells"]["provider_ttm_pe"] = tools.calculate(
                "provider_ttm_pe", row["market_id"], eps["id"]
            )["evidence"]["id"]
        except ValueError:
            missing(
                "TTM 市盈率未计算：EPS 必须为正，且报价、币种、获取时间和拆股口径须匹配。已有 EPS 与行情可分别查看出处；可刷新或改查年度估值。",
                ("provider_ttm_pe",),
            )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        missing(f"TTM EPS 获取未完成（{type(exc).__name__}）；其他结果保留，可刷新重试。")


def validate_comparison(result: dict, ledger: dict, domain, as_of: date) -> None:
    """Offline replay checks the chart and the meaning/period of each table reference."""
    with_valuation = result["schema_version"] == VALUATION_SCHEMA
    with_benchmark = result["schema_version"] == BENCHMARK_SCHEMA or (
        with_valuation and "benchmark" in result
    )
    if (
        result["schema_version"] not in (SCHEMA, BENCHMARK_SCHEMA, VALUATION_SCHEMA)
        or result["note"] != comparison_note(with_benchmark, with_valuation)
        or with_valuation
        and result.get("include_ttm_valuation") is not True
        or not with_valuation
        and "include_ttm_valuation" in result
        or not with_benchmark
        and "benchmark" in result
    ):
        raise ValueError("Unknown comparison schema")
    request = ComparisonRequest(
        tickers=result["tickers"],
        period=result["period"],
        as_of=result["as_of"],
        benchmark=result["benchmark"]["ticker"] if with_benchmark else None,
        include_ttm_valuation=with_valuation,
    )
    if request.as_of != as_of or [r["ticker"] for r in result["rows"]] != request.tickers:
        raise ValueError("Comparison scope differs from its research")
    market_ids = []
    facts = {"revenue", "operating_income", "net_income", "operating_cash_flow", "cash_capex"}
    for row in result["rows"]:
        ticker = row["ticker"]
        if row["market_id"]:
            market = MarketEvidence.model_validate(ledger[row["market_id"]]).snapshot
            validate_market_scope(market, ticker, request.period, as_of)
            market_ids.append(row["market_id"])
        basis = ledger[row["annual_basis_id"]] if row["annual_basis_id"] else None
        if basis and (
            basis["kind"] != "fact"
            or basis["ticker"] != ticker
            or not basis["start"]
            or evidence_period(basis) != "annual"
            or basis["filing"]["form"] not in domain.annual_forms(ticker)
        ):
            raise ValueError("Comparison requires an annual fact basis")
        for key, evidence_id in row["cells"].items():
            item = ledger[evidence_id]
            if key in ("provider_ttm_eps", "provider_ttm_pe"):
                if not with_valuation or item["ticker"] != ticker:
                    raise ValueError("Comparison TTM cell company/schema mismatch")
                if key == "provider_ttm_eps":
                    eps = ValuationEvidence.model_validate(item).snapshot
                    if (
                        eps.retrieved_at.date() > as_of
                        or eps.period_end_label > as_of
                        or not row["market_id"]
                        or ledger[row["market_id"]]["snapshot"]["instrument_type"] != "EQUITY"
                        or ticker in domain.financial_profiles
                    ):
                        raise ValueError("Comparison EPS unavailable as of research date")
                else:
                    if (
                        item.get("operation") != "provider_ttm_pe"
                        or item["input_ids"]
                        != [row["market_id"], row["cells"].get("provider_ttm_eps")]
                        or ticker in domain.financial_profiles
                    ):
                        raise ValueError("Comparison TTM P/E inputs/share class mismatch")
                    replay = provider_pe(
                        SimpleNamespace(as_of=as_of, ledger={}),
                        [ledger[row["market_id"]], ledger[row["cells"]["provider_ttm_eps"]]],
                    )["evidence"]
                    if replay != item:
                        raise ValueError("Comparison TTM P/E replay mismatch")
                continue
            if not basis or item["ticker"] != ticker or item["fiscal_year"] != basis["fiscal_year"]:
                raise ValueError("Comparison cell company/year mismatch")
            if key in facts:
                if (
                    item["kind"] != "fact"
                    or item["metric"] != key
                    or (item["start"], item["end"], item["filing_id"])
                    != (basis["start"], basis["end"], basis["filing_id"])
                ):
                    raise ValueError("Comparison financial cell scope mismatch")
            elif key.endswith("_growth") and key[:-7] in facts:
                if item.get("operation") != "growth" or item["input_ids"][0] != row["cells"].get(
                    key[:-7]
                ):
                    raise ValueError("Comparison growth input mismatch")
            elif key in ("operating_margin", "net_margin"):
                numerator = "operating_income" if key == "operating_margin" else "net_income"
                if item.get("operation") != "ratio" or item["input_ids"] != [
                    row["cells"].get(numerator),
                    row["cells"].get("revenue"),
                ]:
                    raise ValueError("Comparison margin input mismatch")
            elif key == "free_cash_flow":
                if item.get("operation") != "free_cash_flow" or item["input_ids"] != [
                    row["cells"].get("operating_cash_flow"),
                    row["cells"].get("cash_capex"),
                ]:
                    raise ValueError("Comparison gross FCF input mismatch")
            elif key == "annual_pe":
                if (
                    item.get("operation") != "annual_pe"
                    or item["input_ids"][0] != row["market_id"]
                    or (item["inputs"][1]["start"], item["inputs"][1]["end"])
                    != (basis["start"], basis["end"])
                ):
                    raise ValueError("Comparison annual P/E input mismatch")
            else:
                raise ValueError("Unknown comparison cell")
            if not Decimal(item["value"]).is_finite():
                raise ValueError("Nonfinite comparison value")
        if with_valuation:
            for key in ("provider_ttm_eps", "provider_ttm_pe"):
                if key not in row["cells"] and not any(
                    key in issue.get("missing", []) and issue.get("message")
                    for issue in row["issues"]
                    if issue["scope"] == "valuation"
                ):
                    raise ValueError("Missing comparison TTM input requires a visible issue")
    benchmark_id = None
    if with_benchmark:
        benchmark = result["benchmark"]
        definition = {k: v for k, v in benchmark.items() if k not in ("market_id", "issues")}
        if definition != {"ticker": request.benchmark, **BENCHMARKS[request.benchmark]}:
            raise ValueError("Benchmark definition mismatch")
        benchmark_id = benchmark["market_id"]
        if benchmark_id:
            market = MarketEvidence.model_validate(ledger[benchmark_id]).snapshot
            validate_market_scope(market, request.benchmark, request.period, as_of)
            if market.instrument_type != "ETF" or market.currency != benchmark["currency"]:
                raise ValueError("Benchmark listing type or currency mismatch")
        elif not benchmark["issues"]:
            raise ValueError("Missing benchmark requires a visible issue")
    if result["chart"] != comparison_chart(market_ids, ledger, benchmark_id):
        raise ValueError("Rebased chart differs from observed market snapshots")
