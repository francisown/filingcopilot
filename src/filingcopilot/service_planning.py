from __future__ import annotations

import re
from datetime import date

from .annual_numbers import numeric_annual_question
from .cashflow import cashflow_explanation, cashflow_scope, is_cashflow_question
from .comparison import question_benchmark, question_ttm_companies
from .fundamentals import is_stock_question
from .historical_valuation import is_historical_valuation_question
from .localization import text as localized_text
from .market import (
    is_market_question,
    is_valuation_question,
    question_period,
    unsupported_question_period,
)
from .models import ResearchRequest
from .peers import candidate_peers, is_peer_question
from .quarterly import (
    fiscal_quarter_period_end,
    is_quarterly_question,
    requested_fiscal_quarter,
)
from .trends import is_financial_trend_question
from .ttm import ADDITIVE_METRICS, is_ttm_question
from .updates import is_stock_updates_question, is_updates_question, wants_update_summary
from .valuation import is_provider_valuation_question


def _forecast_boundary(question: str) -> str | None:
    """Keep prospective price/certainty requests out of historical data routes.

    Check both word orders, without treating ordinary peer risks or historical
    outperformance as a price forecast. This runs before peer/quarter routing.
    """
    if re.search(
        r"(?:一定|必定|保证|肯定).{0,20}(?:跑赢|跑输)|"
        r"(?:是否|能否|可否|有没有).{0,24}(?:证明|保证|确定).{0,24}(?:跑赢|跑输|超过同行|落后同行)|"
        r"(?:年报|财报|annual report|filing).{0,40}(?:能否|是否|可否|能不能).{0,24}(?:证明|保证|确定).{0,24}(?:跑赢|跑输|超过同行|outperform|underperform)|"
        r"\b(?:will|guaranteed? to|certain to)\b[^.!?;]{0,80}\b(?:outperform|underperform)\b|"
        r"\b(?:outperformance|underperformance)\b[^.!?;]{0,30}\b(?:proven|guaranteed|certain)\b",
        question, re.I,
    ):
        return "不能根据已披露财报保证未来跑赢或跑输同行。缺口是未来经营结果、市场预期与可比期间的收益证据；历史表现和估值倍数不能证明确定的未来回报。应保留不确定性，可先研究已披露风险或比较历史表现。"
    if re.search(
        r"(?:明天|下周|下个月|下季(?:度)?|未来).{0,24}(?:股价|涨到|涨多少|跌多少)|"
        r"(?:股价|股票).{0,24}(?:预测|保证|会不会涨|会不会跌|目标价)|"
        r"(?:预测|预估|目标价).{0,24}股价|"
        r"\b(?:predict|forecast|project|estimate)\b[^.!?;]{0,80}\b(?:stock |share )?price\b|"
        r"\b(?:stock |share )?price\b[^.!?;]{0,40}\b(?:predict|prediction|forecast|target)\b|"
        r"\bwill\b[^.!?;]{0,40}\b(?:stock |share )price\b[^.!?;]{0,20}\b(?:rise|fall|reach|be)\b|"
        r"\b(?:what|where|how much)\b[^.!?;]{0,25}\bwill\b[^.!?;]{0,40}\b(?:stock |share )?price\b",
        question, re.I,
    ):
        return "年报披露经营事实和风险，不能单独确定未来股价。缺口是未来经营结果、市场预期和估值假设；历史报价或已披露季度数字不能代替预测。本系统不提供确定股价预测或保证涨跌，可先研究历史表现和披露风险。"
    return None


def _market_failure_policy(question: str) -> bool:
    """Recognize a request for handling rules, not an actual quote retry."""
    return bool(
        re.search(r"行情|报价|实时价格|market source|quote", question, re.I)
        and re.search(r"不可用|失败|unavailable|fails?|failure", question, re.I)
        and re.search(r"如果|时|\bif\b|\bwhen\b", question, re.I)
        and re.search(r"应如何|如何返回|哪些结论|how should|which conclusions", question, re.I)
    )


class PlanningMixin:
    def _ttm_comparison_plan(self, request: ResearchRequest, labels: list[str]) -> dict:
        self.refresh_domain()
        tickers, unresolved = [], []
        for label in labels:
            matches = {
                ticker
                for ticker, company in self.domain.companies.items()
                if label.casefold()
                in {term.casefold() for term in (ticker, company.name, *company.aliases)}
            }
            matches.update(row["ticker"] for row in self.market.directory.lookup(label))
            if len(matches) != 1 or next(iter(matches), None) not in self.domain.companies:
                unresolved.append(label or "空公司名")
            else:
                tickers.append(next(iter(matches)))
        blocked = None
        if not 2 <= len(labels) <= 4 or len(set(tickers)) != len(tickers):
            blocked = "请明确选择 2 至 4 家不同公司，别名与股票代码指向同一家公司时不要重复。"
        elif unresolved:
            blocked = (
                "尚未明确匹配公司："
                + "、".join(unresolved)
                + "。此提问入口使用已登记公司；请在“公司对比”中搜索并选择具体上市代码，勾选自动准备年报完成首次登记。所有公司确认后再查询，不会忽略未匹配的公司。"
            )
        elif request.as_of != date.today():
            blocked = "公司 TTM EPS / 市盈率比较仅支持今天的供应商版本。请把知悉日期改为今天；历史走势仍可在“公司对比”中单独查询。"
        elif re.search(r"(?<!\d)20\d{2}(?!\d)", request.question):
            blocked = "当前 TTM EPS 比较使用今天获取的供应商版本。请移除指定年份；历史 EPS 与预测 EPS 比较尚未接入。"
        elif unsupported_question_period(request.question) or question_period(request.question) not in ("1Y", "5Y", "MAX"):
            blocked = "公司对比的走势支持一年、五年和全部历史。请改用这些区间；TTM EPS 与市盈率仍是当前版本。"
        return {
            "ready": blocked is None,
            "kind": "company_comparison",
            "uses_model": False,
            "annual_numeric_direct": False,
            "time_budget_seconds": self.settings.research_timeout,
            "comparison_tickers": tickers if blocked is None else [],
            "message": blocked
            or (
                "明确比较 "
                + "、".join(tickers)
                + "：查询当前供应商 TTM 每股收益、同币种报价与市盈率，以及所选期间的共同日期走势；另表补充已缓存年度数字，本次不检查年报新版本。各公司 EPS 期末标签可能不同；倍数高低不代表贵贱。数字和引用由代码生成，无需模型。"
            ),
            "suggestions": ["比较微软和苹果的 TTM 每股收益和市盈率，近五年走势"] if blocked else [],
            "scope": "问题中逐一确认的公司；当前供应商 TTM 估值与年度财务分列。",
        }

    def _peer_comparison_plan(self, request: ResearchRequest) -> dict:
        self.refresh_domain()
        focus = request.ticker.upper() if request.ticker else None
        scope = self.domain.company_scope(request.question)
        if len(scope) == 1:
            focus = next(iter(scope))
        if len(scope) > 1:
            focus = None
        if not focus:
            focus = next(iter(scope)) if len(scope) == 1 else None
        if not focus or focus not in self.domain.companies:
            return {
                "ready": False,
                "kind": "company_comparison",
                "uses_model": False,
                "annual_numeric_direct": False,
                "time_budget_seconds": self.settings.research_timeout,
                "comparison_tickers": [],
                "message": "请先选择一家公司或明确股票代码，再查询候选同行估值。",
                "suggestions": ["选择 AMZN 后查询同行市盈率与近五年走势"],
                "scope": "仅使用已登记证券的候选同行；不会猜测未登记代码。",
            }
        selection = candidate_peers(focus, set(self.domain.companies))
        tickers = [focus, *selection.get("peers", [])]
        blocked = None
        if not selection.get("found") or len(tickers) < 2:
            blocked = "当前已登记证券不足以组成候选同行比较；请在公司对比中手动选择至少两只证券。"
        elif request.as_of != date.today():
            blocked = "候选同行估值使用今天的供应商 TTM EPS 与报价。历史日期可比较共同走势，但历史供应商 EPS 尚未接入。"
        elif unsupported_question_period(request.question) or question_period(request.question) not in ("1Y", "5Y", "MAX"):
            blocked = "候选同行走势支持一年、五年和全部历史；请改用这些区间。"
        message = blocked or (
            f"已为 {focus} 选择候选同行："
            + "、".join(selection["peers"])
            + "。将分别获取当前供应商 TTM EPS、同币种报价和市盈率，并比较所选期间共同日期走势。"
            + "候选组不是正式行业分类，倍数高低不能单独判断贵贱。"
        )
        return {
            "ready": blocked is None,
            "kind": "company_comparison",
            "uses_model": False,
            "annual_numeric_direct": False,
            "time_budget_seconds": self.settings.research_timeout,
            "comparison_tickers": tickers if blocked is None else [],
            "message": message,
            "suggestions": ["在公司对比中手动选择 2 至 4 家证券"] if blocked else [],
            "scope": "候选同行由本地可审查名录选择；同币种和期间仍逐列核验。",
            "peer_selection": selection,
        }

    def _research_plan(self, request: ResearchRequest) -> dict:
        if request.depth == "full":
            supported = bool(request.ticker) and self._filing_scope(request.ticker) is not False
            ready = supported and request.as_of <= date.today()
            return {
                "kind": "company_report",
                "ready": ready,
                "message": "按六个主题整理公司研究，自动检查披露，逐项核验数字与分析。"
                if ready
                else "请先选择支持 SEC 披露的明确公司，并使用今天或更早的知悉日期。",
                "suggestions": ["选择公司后使用完整公司研究"],
            }
        failure_policy = _market_failure_policy(request.question)
        boundary = (
            "如果行情源不可用，应明确报告行情缺口，暂停依赖该报价的市盈率、回报和价格判断；仍可保留通过来源与日期核验的年报数字及披露分析，不得编造价格或把旧报价当作当前报价。此处说明系统处理规则，未实际触发行情故障或执行故障注入测试。"
            if failure_policy else _forecast_boundary(request.question)
        )
        if boundary:
            if request.as_of > date.today():
                boundary = f"知悉日期不能晚于当前可用日期 {date.today().isoformat()}，请调整日期后继续。"
            return {
                "ready": False,
                "kind": "filing",
                "uses_model": False,
                "annual_numeric_direct": False,
                "time_budget_seconds": self.settings.research_timeout,
                "message": boundary,
                "suggestions": ["过去五年股价涨跌和最大回撤是多少？", "年报披露了哪些主要风险？"],
                **({"guidance_only": True} if failure_policy else {}),
            }
        comparison_labels = question_ttm_companies(request.question)
        if comparison_labels is not None:
            return self._ttm_comparison_plan(request, comparison_labels)
        if is_peer_question(request.question):
            return self._peer_comparison_plan(request)
        if is_historical_valuation_question(request.question):
            ready = bool(request.ticker or len(self.domain.company_scope(request.question)) == 1)
            if request.as_of != date.today():
                ready = False
            return {
                "ready": ready,
                "kind": "historical_valuation",
                "uses_model": False,
                "annual_numeric_direct": False,
                "time_budget_seconds": self.settings.research_timeout,
                "comparison_tickers": [],
                "message": (
                    "历史市盈率使用今天获取的日线与各日期前已公开的年度稀释 EPS；"
                    "请把知悉日期设为今天并选择一家公司。"
                    if not ready
                    else "将按实际有效点重建历史年度市盈率，并把无匹配日期按原因留空；这不是目标价或贵贱结论。"
                ),
                "suggestions": ["查询过去五年历史市盈率区间"] if not ready else [],
                "scope": "日线与公开年度 EPS 同币种、股份基础及拆股状态逐点核验。",
            }
        comparison_scope = self.domain.company_scope(request.question)
        if (
            len(comparison_scope) > 1
            and is_provider_valuation_question(request.question)
            and not is_stock_question(request.question)
        ):
            plan = self._ttm_comparison_plan(request, sorted(comparison_scope))
            if plan["ready"]:
                plan.update(
                    ready=False,
                    comparison_tickers=[],
                    message="请把全部公司名称放在 TTM 前，例如“比较微软和苹果的 TTM 每股收益和市盈率”。直接数字比较支持当前 TTM EPS、市盈率及一年、五年或全部历史走势；经营指标、基准或预测 EPS 不能混入此查询，可在公司对比中选择范围，或分别查询。",
                    suggestions=["比较微软和苹果的 TTM 每股收益和市盈率，近五年走势"],
                )
            return plan
        question = request.question
        supplier_assessment = is_provider_valuation_question(question) and is_stock_question(
            question
        )
        capabilities = self.capabilities(request.ticker)
        suggestions = capabilities["questions"][:3]
        blocked = None
        if request.as_of > date.today():
            blocked = f"知悉日期不能晚于当前可用日期 {date.today().isoformat()}，请调整日期后继续。"
        elif re.search(
            r"天气|做菜|菜谱|写诗|写代码|编程|翻译全文|weather|recipe|write code", question, re.I
        ):
            blocked = (
                "这里是股票与财报工作台。可以查询价格、长期表现、年度财务、经营情况和年报风险。"
            )
        elif unsupported_question_period(question):
            blocked = "当前走势图支持一个月、六个月、一年、三年、五年、十年和全部历史；请改用已支持的时间范围。"
        elif (
            re.search(
                r"(?:FY\s*)?20\d{2}\s*Q[1-4]|\bQ[1-4]\b|(?:第)?[一二三四1-4](?:季度|财季)",
                question,
                re.I,
            )
            and is_quarterly_question(question)
            and not is_market_question(question)
        ):
            target = requested_fiscal_quarter(question)
            available = False
            local_quarters = [
                f
                for f in self.corpus.catalog(request.as_of, request.ticker.upper())
                if f["form"] in ("10-Q", "10-Q/A")
            ] if request.ticker else []
            if target and request.ticker:
                available = fiscal_quarter_period_end(
                    self.tools(request.as_of), request.ticker, *target
                ) is not None
            # Let the normal quarterly preparation stage acquire the rolling
            # window on a first request; only reject a named period once local
            # evidence exists and proves that period is absent.
            if local_quarters and not available:
                blocked = "指定历史财季尚未在本地 10-Q 序列中找到；先准备季报后再查询，系统会保留实际报告起止日期，找不到时不会用最新季度替代。"
        elif is_ttm_question(question) and re.search(r"(?<!\d)20\d{2}(?!\d)", question):
            blocked = "TTM 按知悉日期选择最新披露的实际窗口。请用日期控件选择历史日期，或改问 FY 年度财务，避免把年度与滚动期间混用。"
        elif is_provider_valuation_question(question) and request.as_of != date.today():
            blocked = "供应商 TTM EPS 暂无历史发布版本，仅支持今天；历史研究可查询 SEC 年度财务或 TTM 营收、利润率和现金流。"
        elif is_provider_valuation_question(question) and (
            any(m != "diluted_eps" for m in self.domain.allowed_metrics(question))
            or self.domain.requests(question)[1]
        ):
            blocked = "供应商 TTM 每股收益与 SEC TTM 财务流量的期间口径不同。请分别查询“供应商 TTM 每股收益和市盈率”及“TTM 营收、利润率和现金流”。"
        elif (
            is_ttm_question(question)
            and not is_provider_valuation_question(question)
            and (
                re.search(r"市盈率|每股|EPS|P/E|\bPE\b", question, re.I)
                or any(m not in ADDITIVE_METRICS for m in self.domain.allowed_metrics(question))
            )
        ):
            blocked = "TTM 营收、利润、现金流及利润率已支持。每股数需匹配加权股数、稀释及拆股；余额是时点数据，不能套用流量加减。TTM 市盈率暂待匹配 EPS 口径。可查询“年度 EPS 口径市盈率”或“TTM 净利率和经营现金流”。"
        elif re.search(
            r"预测市盈率|forward.{0,5}(?:P/E|PE)|季度市盈率",
            question,
            re.I,
        ):
            blocked = "当前市盈率使用已公开年度稀释 EPS，尚未匹配连续四季或预测 EPS。可先查询“年度 EPS 口径市盈率”或“最新季度营收及同比”。"
        unsupported_benchmark = bool(
            re.search(r"沪深|上证|恒生|罗素|道琼斯|Russell|Dow Jones", question, re.I)
            and re.search(r"股价|走势|涨跌|跑赢|跑输|price|perform", question, re.I)
            and re.search(
                r"基准|大盘|相比|比较|对比|compar|outperform|underperform", question, re.I
            )
            and not re.search(
                r"营收|利润|现金流|原因|为什么|风险|revenue|profit|why|risk", question, re.I
            )
        )
        kind = (
            "benchmark"
            if question_benchmark(question) or unsupported_benchmark
            else "trend"
            if is_financial_trend_question(question)
            else "historical_valuation"
            if is_historical_valuation_question(question)
            else "stock"
            if is_stock_updates_question(question)
            else "updates"
            if is_updates_question(question)
            else "stock"
            if supplier_assessment
            else "provider_valuation"
            if is_provider_valuation_question(question)
            else "market"
            if is_market_question(question)
            else "cashflow"
            if is_cashflow_question(question)
            and (not is_stock_question(question) or is_ttm_question(question))
            else "ttm"
            if is_ttm_question(question)
            else "stock"
            if is_stock_question(question)
            else "quarterly"
            if is_quarterly_question(question)
            else "valuation"
            if is_valuation_question(question)
            else "filing"
        )
        external_only_updates = bool(
            capabilities["external_updates"]
            and kind in ("updates", "stock")
            and is_updates_question(question)
        )
        if (
            kind not in ("market", "benchmark", "provider_valuation", "historical_valuation")
            and self._filing_scope(request.ticker) is False
            and not external_only_updates
        ):
            blocked = "此代码当前支持行情和长期走势，财报采集尚未接入这个市场或产品。可先查询价格、区间涨跌和最大回撤；年度财务研究请选择 SEC 美国公司。"
        if kind == "benchmark":
            if unsupported_benchmark:
                blocked = "目前基准已接入 SPY 标普500 ETF 和 QQQ 纳斯达克100 ETF；此问题指定的基准尚未接入。请选择下方可用基准后继续。"
                suggestions = ["近五年股价与 SPY 基准比较", "近五年股价与 QQQ 基准比较"]
            elif question_period(question) not in ("1Y", "5Y", "MAX"):
                blocked = "基准比较已支持一年、五年和全部历史；请改选这些区间后继续。"
                suggestions = [
                    "近一年股价与 SPY 基准比较",
                    "近五年股价与 QQQ 基准比较",
                    "全部历史股价与大盘基准比较",
                ]
            scope = self.domain.company_scope(question)
            if len(scope) > 1 or request.ticker and scope and scope != {request.ticker}:
                blocked = "这次提问按一只明确选择的证券与 ETF 基准比较；多家公司可在“公司对比”中逐一选择后查询。"
        if capabilities["annual_only"] and kind not in ("market", "trend", "benchmark"):
            try:
                native_cash_period = cashflow_scope(question)[0] if kind == "cashflow" else None
            except ValueError:
                native_cash_period = None
            unsupported_cash_adjustment = bool(
                (
                    kind == "cashflow"
                    or self.domain.allowed_metrics(question) & {"cash_capex", "operating_cash_flow"}
                )
                and re.search(
                    r"净(?:现金)?(?:资本开支|购置|自由现金流)|"
                    r"(?:资本开支|购置|自由现金流).{0,8}净额|"
                    r"net (?:cash )?(?:capex|capital expenditures|PP&E purchases|free cash flow)|"
                    r"政府补助|government grants?",
                    question,
                    re.I,
                )
            )
            native_cashflow = (
                kind == "cashflow"
                and capabilities["annual_cashflow"]
                and native_cash_period in ("annual", "latest")
                and not is_quarterly_question(question)
                and not is_ttm_question(question)
                and not unsupported_cash_adjustment
                and self.domain.allowed_metrics(question) <= {"operating_cash_flow", "cash_capex"}
            )
            native_valuation = (
                kind == "valuation"
                and capabilities["annual_ads_valuation"]
                and not is_quarterly_question(question)
                and not is_ttm_question(question)
            )
            if (
                not (
                    kind == "updates"
                    and capabilities["company_updates"]
                    and not (
                        (is_quarterly_question(question) or is_ttm_question(question))
                        and self.domain.allowed_metrics(question)
                    )
                )
                and not native_cashflow
                and not native_valuation
                and (
                    unsupported_cash_adjustment
                    or kind != "filing"
                    or not self.domain.allowed_metrics(question)
                    <= set(capabilities["annual_financial_metrics"])
                )
            ):
                blocked = (
                    "此证券支持 20-F 年报原文、年度财务和现金流毛额，以及原件单独披露的美元 ADS 年度市盈率，并可查 6-K 官方公告原文；季度财务计算、净资本开支和 TTM ADS 口径仍待接入。可查询年度数字、风险，或价格与长期走势。"
                    if capabilities["annual_financial_metrics"]
                    else "此证券支持 20-F 年报原文中的业务与风险，以及价格和长期走势；财务数字、6-K 与完整估值尚未接入。"
                )
                next_question = (
                    next(q for q in capabilities["questions"] if "自由现金流" in q)
                    if capabilities["annual_cashflow"]
                    and (kind == "cashflow" or unsupported_cash_adjustment)
                    else capabilities["questions"][2]
                )
                suggestions = [next_question, *capabilities["questions"][:2]]
        annual_numeric_direct = bool(
            blocked is None and capabilities["filings_supported"] is not False
            and (not capabilities["annual_only"] or capabilities["annual_financial_metrics"])
            and kind == "filing" and len(self.domain.company_scope(question)) <= 1
            and numeric_annual_question(question, self.domain)
        )
        if kind == "valuation" and len(set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", question))) > 1:
            blocked = "请一次选择一个 EPS 财年查询年度市盈率；当前报价会与该年报 EPS 配对。"
        if "ads_diluted_eps" in self.domain.allowed_metrics(question) and re.search(
            r"同比|增长|差额|比率|growth|increase|difference|ratio", question, re.I
        ):
            blocked = "各年报美元 ADS EPS 采用各自便利换算汇率，未对齐前不计算同比、差额或比率。可查询年报 ADS EPS 报告值或年度市盈率；盈利增长可查原币净利润。"
            suggestions = [
                "年报美元 ADS 稀释每股收益是多少？",
                "年度美元 ADS EPS 口径市盈率是多少？",
                "年报净利润同比是多少？",
            ]
        message = (
            blocked
            or {
                "benchmark": "按共同观察日期比较所选股票与 SPY / QQQ ETF 的一年、五年或全部历史，计算涨跌差（百分点）；不换汇，不把 ETF 价格称作指数点位，无需模型。",
                "trend": "读取近三年或五年的完整年度事实，缺少资料时自动准备年报；图表、数值和引用由代码生成，缺失年份不补零，不调用模型。",
                "market": "直接读取行情并计算区间表现，无需模型或先下载年报。",
                "provider_valuation": "直接读取供应商 TTM 稀释 EPS 与当前报价，并计算市盈率；只支持今天。期末标签可能经过日历标准化，未逐季重建 SEC 稀释股数。",
                "updates": "自动检查知悉日期前近 90 天的 SEC 8-K 公司公告和随附发布；显示申报日、事件日与原文引用。最多五次申报，尚不覆盖全部媒体新闻。",
                "valuation": "读取当前报价与年度稀释 EPS，代码计算年度口径市盈率；不混作 TTM。",
                "stock": "以提问指定的期间为重点，结合年度与最新可用季报、历史行情、SEC 年度与供应商 TTM 市盈率及披露风险形成有条件的研究判断。单季盈利与累计现金流分别注明期间。供应商 TTM 仅支持当前日期，历史研究保留年度口径。",
                "filing": "按问题读取年度事实、计算同比或利润率，并检索申报原文。",
                "quarterly": "先检查知悉日期前最新 SEC 10-Q，区分单季度与累计事实并计算同口径同比。原因和风险问题在 Agent 模式下检索季报原文并核验解释。",
                "cashflow": "检查年报和最新累计季报，按年报＋本年累计－上年同期累计拼接 TTM 自由现金流及构成项，核对实际窗口与净购置调整。"
                if is_ttm_question(question)
                else "核对原始现金流表、实际期间及现金 PP&E 购置，按明确公式计算自由现金流、变化额及可比同比。AMZN 净口径另绑定出售及激励收款。",
                "ttm": "先检查年报和最新累计季报，拼接 TTM 流量；利润率用同窗口、同申报版本的 TTM 利润除以收入计算。显示实际财年日期与递归证据，不估算尚未披露月份。",
            }[kind]
        )
        if blocked is None and capabilities["annual_only"] and kind == "filing":
            message = (
                "代码从原始 20-F 查询申报原币种年度数字、同比与利润率，无需模型；普通股 EPS 不用于美元 ADS 市盈率。"
                if annual_numeric_direct
                else "自动获取知悉日期前的 SEC 20-F 年报原文与已登记年度数字，检索业务与风险并引用出处；季度财务计算、TTM 与完整估值仍待接入。"
            )
            if annual_numeric_direct and "ads_diluted_eps" in self.domain.allowed_metrics(question):
                message = "读取 20-F 原件单独披露的美元 ADS 年度稀释 EPS；美元为该年报固定汇率的便利换算，无需模型，不从普通股 EPS 推算。"
        elif blocked is None and annual_numeric_direct:
            message = "代码查询所需年度数字、同比或利润率并核对出处；缺少指定年份时只补该年报，无需模型或向量索引。"
        if blocked is None and capabilities["annual_ads_valuation"] and kind == "valuation":
            message = "读取美元 ADS 报价与 20-F 原件的美元 ADS 年度稀释 EPS，代码计算市盈率；EPS 采用年报固定汇率便利换算，不是 TTM 或当前汇率重算。"
        if blocked is None and capabilities["annual_only"] and kind == "cashflow":
            message = "从原始 SEC 20-F 提取年度申报原币种经营现金流和现金 PP&E 支付毛额，计算自由现金流、变化额与同口径同比；未抵减政府补助和出售收款。最新查询仅覆盖最新已披露完整年度。"
        if blocked is None and kind == "updates" and capabilities["update_form"] == "6-K":
            message = "自动读取近 90 天最多五次 SEC 6-K 官方公告，显示公开日、报告标注日期及逐字原文引用，无需先下载年报或调用模型；不重建季度财务或 TTM。"
        if blocked is None and kind == "updates" and wants_update_summary(question):
            message = (
                "自动获取近 90 天最多五次官方公告，生成简短中文摘要并逐条审查出处；公开日期由来源显示，6-K 报告标注日期不视作事件发生日。摘要不推断股价影响，失败时保留原文。"
                if request.mode == "agent" and self.gateway is not None
                else "本次可直接获取公告原文；中文摘要需要已配置模型的 Agent 模式。先查看原文，或切换 Agent 后重试中文摘要。"
            )
        if blocked is None and kind == "stock" and is_stock_updates_question(question):
            if request.mode == "reference":
                message = "本次无模型股票研究提供财务证据检索；同时生成经营、价格判断和公告摘要，请切换 Agent 研究。公告原文可单独查询“最近有哪些公司公告？”。"
            else:
                message += " 同时检查近 90 天官方公告，摘要最近两次有可读原文的申报，逐条保留公开日与引用；其余公告可单独查询，不推断公告造成的股价变化。"
        return {
            "ready": blocked is None,
            "time_budget_seconds": self.settings.research_timeout,
            "kind": kind,
            "annual_numeric_direct": annual_numeric_direct,
            "uses_model": request.mode == "agent"
            and kind in ("stock", "filing")
            and not annual_numeric_direct
            or request.mode == "agent"
            and kind == "updates"
            and (capabilities["update_form"] != "6-K" or wants_update_summary(question))
            and self.gateway is not None
            or request.mode == "agent"
            and kind == "quarterly"
            and self.gateway is not None
            and bool(
                re.search(
                    r"为什么|为何|原因|风险|解释|驱动|why|risk|explain|driver", question, re.I
                )
                or is_stock_question(question)
            )
            or request.mode == "agent"
            and kind == "cashflow"
            and self.gateway is not None
            and cashflow_explanation(question),
            "message": message,
            "suggestions": suggestions if blocked else [],
            "scope": capabilities["scope"],
        }

    def _estimate_budget(self, request: ResearchRequest, plan: dict) -> dict:
        """Return a transparent, deliberately conservative run estimate.

        This is a planning signal, not a price quote: provider latency and
        prompt size vary. A configured per-million-token price enables a USD
        upper estimate; without it the UI explicitly says that cost is unknown.
        """
        kind = plan.get("kind", "filing")
        uses_model = bool(plan.get("uses_model"))
        if request.depth == "full":
            uses_model = uses_model or (request.mode == "agent" and self.gateway is not None)
        model_calls = 0
        if uses_model:
            model_calls = min(
                self.settings.max_rounds,
                2 if kind in ("updates", "quarterly", "cashflow") else 4,
            )
            if request.depth == "full":
                model_calls = min(self.settings.max_rounds, 3)
        direct_tools = {
            "market": 1,
            "benchmark": 2,
            "trend": 2,
            "valuation": 2,
            "provider_valuation": 2,
            "historical_valuation": 3,
            "updates": 3,
            "quarterly": 5,
            "cashflow": 6,
            "ttm": 6,
            "filing": 3,
            "stock": 8,
            "company_comparison": 4,
            "company_report": 14,
        }
        tool_calls = min(self.settings.max_tool_calls, direct_tools.get(kind, 3))
        if uses_model:
            tool_calls = min(self.settings.max_tool_calls, tool_calls + min(model_calls, 4))
        prompt_tokens = model_calls * 2500
        completion_tokens = model_calls * min(self.settings.model_max_tokens, 1800)
        total_tokens = prompt_tokens + completion_tokens
        input_price = self.settings.model_input_price_per_million
        output_price = self.settings.model_output_price_per_million
        estimated_cost = None
        if input_price is not None or output_price is not None:
            estimated_cost = round(
                (prompt_tokens / 1_000_000) * (input_price or 0)
                + (completion_tokens / 1_000_000) * (output_price or 0),
                6,
            )
        # Include the configured inter-call spacing in the lower-level model
        # latency estimate; cap at the actual research deadline.
        latency = tool_calls * 0.35 + model_calls * self.settings.model_timeout
        if model_calls > 1:
            latency += (model_calls - 1) * self.settings.min_model_interval
        latency = round(min(float(plan.get("time_budget_seconds", self.settings.research_timeout)), latency), 1)
        return {
            "estimated_model_calls": model_calls,
            "estimated_tool_calls": tool_calls,
            "estimated_prompt_tokens": prompt_tokens,
            "estimated_completion_tokens": completion_tokens,
            "estimated_total_tokens": total_tokens,
            "estimated_cost_usd": estimated_cost,
            "estimated_latency_seconds": latency,
            "time_budget_seconds": plan.get("time_budget_seconds", self.settings.research_timeout),
            "cost_basis": (
                "按 FC_MODEL_*_PRICE_PER_MILLION 计算的模型 token 估算；未配置价格，成本显示未知。"
                if estimated_cost is None
                else "按配置的模型输入/输出每百万 token 价格和保守 token 上限估算。"
            ),
            "latency_basis": "包含模型超时上限、模型最小间隔和工具调用粗略开销；实际网络时间可能更短。",
        }

    def research_plan(self, request: ResearchRequest) -> dict:
        plan = self._research_plan(request)
        if "uses_model" not in plan:
            plan["uses_model"] = request.mode == "agent" and self.gateway is not None
        plan["budget"] = self._estimate_budget(request, plan)
        # Keep these top-level aliases for simple clients and old UI builds.
        for key, value in plan["budget"].items():
            plan.setdefault(key, value)
        return plan

    @staticmethod
    def localize_research_plan(plan: dict, language: str) -> dict:
        """Keep the public planning endpoint in its requested language.

        The browser can translate known Chinese fragments, but CLI, REST and
        MCP callers do not have that DOM layer. If a dynamic plan still has
        untranslated Chinese after the shared catalogue pass, use a concise
        English scope message instead of leaking a mixed-language response.
        """
        if language != "en":
            return plan
        kind = plan.get("kind", "filing")
        ready_fallback = {
            "market": "Query prices and long-term market history for the selected security.",
            "trend": "Read annual financial facts for the requested trend window; missing years remain missing.",
            "updates": "Check recent official company disclosures within the supported public-date window.",
            "cashflow": "Calculate the requested cash-flow measure from period-matched filing facts.",
            "filing": "Read the requested filing facts and source passages with their reporting periods.",
            "stock": "Combine operating performance, cash generation, valuation and disclosed risks with citations.",
            "company_comparison": "Compare the selected companies using aligned market and financial evidence.",
            "benchmark": "Compare the selected security with the supported ETF benchmark over the chosen period.",
            "valuation": "Calculate the supported annual-EPS valuation from the selected quote and filing evidence.",
            "provider_valuation": "Read the provider TTM EPS and quote, then calculate the provider-basis P/E.",
            "historical_valuation": "Rebuild historical annual-EPS P/E points from dated quotes and filings.",
            "company_report": "Organize the selected company research into six evidence-bound sections.",
            "quarterly": "Read the requested quarterly facts with exact filing periods and comparisons.",
            "ttm": "Calculate trailing-period flows and margins from aligned filing facts.",
        }.get(kind, "Review the requested evidence and calculations within the supported data scope.")
        blocked_fallback = "This request is outside the current supported scope. Choose a suggested question or adjust the company, date, or period."
        message = localized_text(str(plan.get("message", "")), "en")
        if re.search(r"[\u4e00-\u9fff]", message):
            message = ready_fallback if plan.get("ready") else blocked_fallback
        plan["message"] = message
        suggestions = [localized_text(str(value), "en") for value in plan.get("suggestions", [])]
        if any(re.search(r"[\u4e00-\u9fff]", value) for value in suggestions):
            suggestions = [] if plan.get("ready") else [
                "What is the stock price and five-year return?",
                "What were the latest annual revenue and net income?",
                "What are the main risks disclosed in the latest annual report?",
            ]
        plan["suggestions"] = suggestions
        if "scope" in plan:
            scope = localized_text(str(plan["scope"]), "en")
            plan["scope"] = scope if not re.search(r"[\u4e00-\u9fff]", scope) else ready_fallback
        budget = plan.get("budget")
        if isinstance(budget, dict):
            budget_fallbacks = {
                "cost_basis": "Model cost is unknown because provider token prices are not configured.",
                "latency_basis": "The estimate includes model timeouts, minimum intervals and rough tool-call overhead; actual network time may be shorter.",
            }
            for key, fallback in budget_fallbacks.items():
                value = localized_text(str(budget.get(key, "")), "en")
                budget[key] = value if not re.search(r"[\u4e00-\u9fff]", value) else fallback
                if key in plan:
                    plan[key] = budget[key]
        return plan
