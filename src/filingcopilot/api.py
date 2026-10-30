from __future__ import annotations

import hmac
import re
import sys
from contextlib import asynccontextmanager
from datetime import date
from importlib.resources import files
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .auth import authorization_token
from .company_page import build_company_page
from .comparison import ComparisonRequest
from .dcf import DcfRequest, dcf_source_inputs, sourced_dcf_sensitivity
from .execution import ResearchControl, ResearchStopped, refresh_scope, research_scope
from .jobs import ResearchJobConflict, ResearchJobs
from .localization import header_language, request_from_header
from .localization import text as translate_text
from .market import MarketError, MarketUnavailable, statistics, symbol
from .models import (
    CompanyLookup,
    CompanyPreparation,
    FollowupRequest,
    ResearchRequest,
    TranslationRequest,
)
from .runtime_identity import RuntimeIdentity
from .scenarios import ScenarioRequest
from .service import FilingCopilot
from .settings import Settings
from .trends import TrendRequest
from .watchlist import WatchlistSelection

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _endpoint_budget(configured: float | None, cap: float) -> float | None:
    """Apply a quick-read cap only when a whole-research deadline is configured."""
    return None if configured is None else min(cap, configured)


def _api_error_message(detail: object, language: str) -> str:
    """Return a safe, request-language error for direct REST callers.

    Service validation errors are assembled from localized plan text and may
    contain a date, suggestions, or an untranslated fragment.  The normal
    HTTP exception handler can translate catalog entries, but it cannot safely
    translate an arbitrary mixed-language sentence.  Keep useful context for
    the common awareness-date and chart-window guards; otherwise use a clear
    language-safe fallback rather than leaking Chinese into an English API
    response.
    """
    message = str(detail)
    if language != "en":
        return message
    # Translate each complete plan message before joining it to suggestions.
    # Otherwise a fragment such as " 年" can consume the start of " 年报…",
    # causing the useful English reason to be replaced by the generic fallback.
    reason, separator, suggestions = message.partition(" 可选问题：")
    translated = translate_text(reason, "en")
    if separator:
        translated += " Suggested questions: " + " / ".join(
            translate_text(value, "en") for value in suggestions.split(" / ")
        )
    if not _CJK.search(translated):
        return translated
    if "知悉日期不能晚于当前可用日期" in message or "Awareness date cannot be later" in message:
        available = re.search(r"\b\d{4}-\d{2}-\d{2}\b", message)
        suffix = f" ({available.group(0)})" if available else ""
        return (
            "The awareness date cannot be later than the current available date"
            f"{suffix}. Adjust the date and try again."
        )
    if "当前走势图支持" in message or "Price charts support" in translated:
        return (
            "Price charts support one month, six months, one year, three years, "
            "five years, ten years, or full history. Choose one of these ranges."
        )
    return "The request is outside the supported scope. Adjust the company, date, or question and try again."


def create_app(service: FilingCopilot | None = None) -> FastAPI:
    service = service or FilingCopilot(Settings.from_env())
    jobs = ResearchJobs(service)

    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.close()

    identity = RuntimeIdentity(__version__)
    app = FastAPI(title="FilingCopilot", version=__version__, lifespan=lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def localized_http_error(request, exc):
        detail = exc.detail
        if isinstance(detail, str):
            language = getattr(request.state, "output_language", header_language(request.headers.get("accept-language")))
            detail = translate_text(detail, language)
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.middleware('http')
    async def require_current_code(request, call_next):
        status = identity.inspect()
        if status['needs_restart'] and (request.method not in ('GET', 'HEAD') or request.url.path in ('/', '/index.html') or request.url.path.startswith('/static/')):
            return JSONResponse({'detail': translate_text('服务代码或页面资源已经更新，请重启当前服务后再开始新任务。', header_language(request.headers.get('accept-language'))), 'runtime': status}, status_code=503)
        return await call_next(request)

    @app.middleware('http')
    async def require_authentication(request, call_next):
        # Lightweight service doubles used by offline API tests do not expose
        # Settings; they represent the default no-token local mode.
        configured = getattr(getattr(service, "settings", None), "auth_token", None)
        if configured is not None:
            supplied = authorization_token(request.headers.get("authorization"))
            if supplied is None or not hmac.compare_digest(supplied, configured):
                return JSONResponse(
                    {"detail": "需要 API 凭证。"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": 'Bearer realm="filingcopilot", Basic realm="filingcopilot"'
                    },
                )
        return await call_next(request)

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        response.headers.setdefault(
            'Permissions-Policy', 'camera=(), geolocation=(), microphone=()'
        )
        return response
    app.state.service = service
    app.state.research_jobs = jobs
    static = str(files("filingcopilot").joinpath("static"))
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static + "/index.html")

    @app.get("/api/health")
    def health():
        service.refresh_domain()
        return {
            "version": __version__,
            "application": "filingcopilot",
            "runtime": identity.inspect(),
            "research_session": jobs.session,
            "as_of_default": date.today().isoformat(),
            "agent_ready": service.gateway is not None,
            "model": service.settings.model or None,
            "review_model": service.settings.review_model or None,
            "review_batch_size": service.settings.review_batch_size,
            "review_configuration_warnings": list(service.settings.review_configuration_warnings),
            "sampling": {
                "temperature": service.settings.model_temperature,
                "top_p": service.settings.model_top_p,
                "seed": service.settings.model_seed,
            },
            "pricing_configured": service.settings.pricing_configured,
            "cost_budget_usd": service.settings.max_cost_usd,
            "embedding_mode": service.settings.embedding_mode,
            "embedding_model": service.settings.embedding_model,
            "rerank_mode": service.settings.rerank_mode,
            "embedding_unavailable": service.retriever.model_error,
            "companies": [
                {"ticker": key, "name": company.name}
                for key, company in service.domain.companies.items()
            ],
            "supported_metrics": list(service.domain.metrics),
            "company_discovery": True,
            "market_data": True,
            "independent_daily_market_configured": service.market.backup is not None,
            "market_sources": service.market.source_status(),
            "company_report": "experimental",
            "valuation_scenarios": True,
            "historical_valuation": True,
            "watchlist": True,
            "research_jobs": True,
            "research_time_budget_seconds": service.settings.research_timeout,
            "sec_acquisition_ready": bool(
                service.settings.sec_user_agent and "@" in service.settings.sec_user_agent
            ),
        }

    @app.get("/api/capabilities")
    def capabilities(ticker: str | None = None):
        return service.capabilities(ticker)

    @app.get("/api/watchlist")
    def watchlist():
        return service.watchlist.items()

    @app.post("/api/watchlist")
    def add_watchlist(request: WatchlistSelection):
        try:
            return service.watchlist.add(request.ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "证券身份查询连接未完成，请保留所选代码并稍后重试。") from exc

    @app.delete("/api/watchlist/{ticker}")
    def remove_watchlist(ticker: str):
        try:
            return service.watchlist.remove(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/financial/trend")
    def financial_trend(request: TrendRequest, accept_language: str | None = Header(default=None)):
        request = request_from_header(request, accept_language)
        try:
            return service.financial_trend(request).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/comparison")
    def comparison(request: ComparisonRequest, accept_language: str | None = Header(default=None)):
        request = request_from_header(request, accept_language)
        try:
            return service.compare_companies(request).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/valuation/scenarios")
    def valuation_scenarios(request: ScenarioRequest, accept_language: str | None = Header(default=None)):
        request = request_from_header(request, accept_language)
        try:
            return service.valuation_scenarios(request).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/valuation/dcf")
    def valuation_dcf(request: DcfRequest):
        try:
            return sourced_dcf_sensitivity(service, request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/companies/{ticker}/dcf-inputs")
    def company_dcf_inputs(ticker: str, as_of: date | None = None):
        try:
            return dcf_source_inputs(service, ticker, as_of or date.today())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/market/history")
    def market_history(
        ticker: str, period: str = "5Y", as_of: date | None = None, refresh: bool = False
    ):
        try:
            snapshot = service.market.history(ticker, period, as_of, refresh=refresh)
            return {
                "snapshot": snapshot.model_dump(mode="json"),
                "statistics": statistics(snapshot),
                **({"availability": service.market.availability(ticker)} if snapshot.stale else {}),
            }
        except MarketUnavailable as exc:
            return JSONResponse(
                {
                    "detail": str(exc),
                    "market_status": {
                        **exc.status,
                        "independent_fallback_configured": True,
                    },
                },
                status_code=503,
                headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"},
            )
        except MarketError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/market/valuation")
    def market_valuation(ticker: str, as_of: date | None = None, basis: str = "annual"):
        try:
            if basis == "provider_ttm":
                tools = service.tools(as_of or date.today())
                result = tools.get_provider_ttm_valuation(ticker)
                if result.get("found"):
                    result["evidence"] = tools.ledger[result["evidence"]["id"]]
                return result
            if basis != "annual":
                raise ValueError("估值口径须为 annual 或 provider_ttm。")
            return service.tools(as_of or date.today()).get_stock_valuation(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/market/historical-valuation")
    def historical_market_valuation(ticker: str, period: str = "5Y", as_of: date | None = None):
        try:
            day = as_of or date.today()
            if day != date.today():
                raise ValueError(
                    "历史市盈率参照目前只支持今天的行情与公开 EPS；请把知悉日期设为今天。"
                )
            tools = service.tools(day)
            result = tools.get_historical_valuation(ticker, period)
            return {**result, "observed_evidence": list(tools.ledger.values())}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/stocks/search")
    def find_stock(request: CompanyLookup):
        try:
            return service.find_stock(request.query, request.translate)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                502, "股票名称查询连接未完成，可输入明确代码直接查看行情。"
            ) from exc
        except Exception as exc:
            raise HTTPException(502, "股票名称查询暂未完成，请输入明确股票代码后重试。") from exc

    @app.get("/api/companies/{ticker}/peers")
    def peer_candidates(ticker: str):
        try:
            return service.peer_candidates(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/companies/{ticker}/research-page")
    def company_research_page(
        ticker: str,
        as_of: date | None = None,
        refresh: bool = False,
        section: str | None = None,
        include_market: bool = True,
    ):
        try:
            control = ResearchControl()
            control.start_budget(_endpoint_budget(service.settings.research_timeout, 40))
            with research_scope(control):
                return build_company_page(
                    service,
                    ticker,
                    as_of or date.today(),
                    refresh=refresh,
                    section=section,
                    include_market=include_market,
                )
        except ResearchStopped as exc:
            raise HTTPException(
                504, "本次公司资料读取时间用完；已取得资料保存在本地，可重试单个区块。"
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/companies/search")
    def find_company(request: CompanyLookup):
        try:
            return service.discovery.search(request.query, request.translate)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                502, f"SEC 公司查找返回 HTTP {exc.response.status_code}，请稍后重试。"
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(502, "连接 SEC 失败，请检查网络后重试。") from exc
        except Exception as exc:
            raise HTTPException(502, "公司名称转换或名录查询失败，请输入股票代码后重试。") from exc

    @app.post("/api/companies/prepare")
    def prepare_company(request: CompanyPreparation):
        try:
            return service.discovery.start(request.ticker, request.as_of, request.years)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/companies/jobs/{job_id}")
    def preparation_job(job_id: str):
        try:
            return service.discovery.job(job_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/catalog")
    def catalog(as_of: date | None = None):
        as_of = as_of or date.today()
        filings = service.corpus.catalog(as_of)
        with service.corpus.connect() as db:
            facts = db.execute(
                "SELECT count(*) FROM facts e JOIN filings d ON d.id=e.filing_id WHERE d.filed<=?",
                (as_of.isoformat(),),
            ).fetchone()[0]
            passages = db.execute(
                "SELECT count(*) FROM passages e JOIN filings d ON d.id=e.filing_id "
                "WHERE d.filed<=?",
                (as_of.isoformat(),),
            ).fetchone()[0]
            statement_facts = db.execute(
                "SELECT count(*) FROM statement_facts e JOIN filings d ON d.id=e.filing_id "
                "WHERE d.filed<=?",
                (as_of.isoformat(),),
            ).fetchone()[0]
        return {
            "filings": filings,
            "fact_count": facts,
            "passage_count": passages,
            "statement_fact_count": statement_facts,
            "as_of": as_of.isoformat(),
        }

    @app.get("/api/companies/{ticker}/quarterly-sequence")
    def quarterly_sequence(
        ticker: str,
        as_of: date | None = None,
        quarters: int = 8,
        refresh: bool = False,
    ):
        try:
            knowledge_date = as_of or date.today()
            ticker = symbol(ticker)
            if knowledge_date > date.today() or not 1 <= quarters <= 12:
                raise ValueError("日期不能晚于今天，季度数量须为 1 至 12。")
            control = ResearchControl()
            control.start_budget(_endpoint_budget(service.settings.research_timeout, 20))
            with research_scope(control):
                preparation = None
                capabilities = service.capabilities(ticker)
                if (
                    refresh
                    and capabilities["filings_supported"]
                    and not capabilities["annual_only"]
                ):
                    try:
                        with refresh_scope(min(12, service.settings.refresh_timeout)):
                            preparation = service.discovery.prepare_quarterly(
                                ticker, knowledge_date
                            )
                    except ResearchStopped:
                        raise
                    except Exception as exc:
                        preparation = {
                            "checked": False,
                            "warnings": [
                                f"季报检查未完成（{type(exc).__name__}），保留已有资料，可稍后重试。"
                            ],
                        }
                tools = service.tools(knowledge_date)
                result = tools.get_quarterly_sequence(ticker, quarters=quarters)
                result["source_preparation"] = preparation
                result["evidence"] = list(tools.ledger.values())
                return result
        except ResearchStopped as exc:
            raise HTTPException(
                504, "季报检查时间用完；已保存资料仍可读取，请稍后重试刷新。"
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/companies/{ticker}/statements")
    def statements(
        ticker: str,
        statement: str = "all",
        period_end: str | None = None,
        filing_id: str | None = None,
        as_of: date | None = None,
    ):
        try:
            ticker = symbol(ticker)
            if as_of and as_of > date.today():
                raise ValueError("知悉日期不能晚于今天。")
            tools = service.tools(as_of or date.today())
            return tools.get_statement(ticker, statement, period_end, filing_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/research/jobs", status_code=202)
    def start_research(
        request: ResearchRequest,
        idempotency_key: str | None = Header(default=None),
        x_research_session: str | None = Header(default=None),
        accept_language: str | None = Header(default=None),
    ):
        request = request_from_header(request, accept_language)
        try:
            if request.as_of > date.today():
                raise ValueError("知悉日期不能晚于今天。")
            return jobs.start(request, idempotency_key, x_research_session)
        except ResearchJobConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/api/research/jobs/recover")
    def recover_research(key: str, x_research_session: str | None = Header(default=None)):
        try:
            return jobs.recover(key, x_research_session)
        except ResearchJobConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/research/jobs/{job_id}")
    def research_job(job_id: str):
        try:
            return jobs.get(job_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/research/jobs/{job_id}/cancel")
    def cancel_research(job_id: str):
        try:
            return jobs.cancel(job_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/research")
    async def research(request: ResearchRequest, accept_language: str | None = Header(default=None)):
        request = request_from_header(request, accept_language)
        try:
            answer = await run_in_threadpool(service.research, request)
            return answer.model_dump(mode="json")
        except ValueError as exc:
            return JSONResponse({"detail": _api_error_message(exc, request.language)}, status_code=422)
        except httpx.HTTPError:
            return JSONResponse({"detail": _api_error_message("SEC 年报准备未完成，请稍后重试。", request.language)}, status_code=502)

    @app.post("/api/research/followup")
    def research_followup(request: FollowupRequest, accept_language: str | None = Header(default=None)):
        request = request_from_header(request, accept_language)
        try:
            return service.followup(request).model_dump(mode="json")
        except ValueError as exc:
            return JSONResponse({"detail": _api_error_message(exc, request.language)}, status_code=422)
        except httpx.HTTPError:
            return JSONResponse({"detail": _api_error_message("追问刷新资料未完成；前一轮研究仍保持不变。", request.language)}, status_code=502)

    @app.post("/api/research/plan")
    def research_plan(request: ResearchRequest, accept_language: str | None = Header(default=None)):
        request = request_from_header(request, accept_language)
        return service.localize_research_plan(service.research_plan(request), request.language)

    @app.post("/api/translate")
    async def translate_answer(request: TranslationRequest):
        try:
            translations = await run_in_threadpool(
                service.translate_texts, request.texts, request.target
            )
            return {"translations": translations, "target": request.target}
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail="模型翻译请求失败，请稍后重试。"
            ) from exc

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str, as_of: date | None = None):
        as_of = as_of or date.today()
        result = service.corpus.evidence(evidence_id, as_of)
        if result is None:
            raise HTTPException(404, "该证据在知悉日期前不可用。")
        return result

    @app.get("/api/runs/{run_id}/market")
    def saved_market(run_id: str):
        from .audit import collect_evidence
        from .market import MarketEvidence

        record = service.corpus.run(run_id)
        if not record:
            raise HTTPException(404, "研究记录不存在。")
        ledger = collect_evidence(record["evidence"])
        item = next((e for e in ledger.values() if e["kind"] == "market"), None)
        if item is None:
            raise HTTPException(404, "此研究没有保存行情快照。")
        snapshot = MarketEvidence.model_validate(item).snapshot
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "statistics": statistics(snapshot),
            "frozen": True,
        }

    @app.get("/api/runs")
    def runs():
        return service.corpus.recent_runs()

    @app.get("/api/runs/{run_id}/package")
    def package(run_id: str):
        try:
            return service.export_package(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/runs/{run_id}/audit")
    def audit(run_id: str):
        try:
            return service.audit_run(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/runs/{run_id}/attribution")
    def attribution(run_id: str, before_run_id: str):
        from .attribution import build_attribution

        try:
            after = service.export_package(run_id)
            before = service.export_package(before_run_id)
            return build_attribution(before, after)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/facts/versions")
    def versions(ticker: str, metric: str, fiscal_year: int, as_of: date | None = None):
        try:
            return service.tools(as_of or date.today()).get_fact_versions(
                ticker, metric, fiscal_year
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/connection")
    def connection():
        return {
            "mcpServers": {
                "filingcopilot": {
                    "command": sys.executable,
                    "args": ["-m", "filingcopilot", "--home", str(service.settings.home), "mcp"],
                    "cwd": str(Path.cwd()),
                    "env": {
                        "PYTHONUTF8": "1",
                        **(
                            {"FC_DOMAIN_CONFIG": str(service.settings.domain_config)}
                            if service.settings.domain_config
                            else {}
                        ),
                    },
                }
            }
        }

    return app
