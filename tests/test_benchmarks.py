import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from test_market import NOW, provider

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, digest
from filingcopilot.comparison import BENCHMARK_SCHEMA, ComparisonRequest, question_benchmark
from filingcopilot.execution import ResearchControl, checkpoint, current_control
from filingcopilot.market import MarketData
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.fixture
def service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))

    def handler(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        data = provider()
        result = data["chart"]["result"][0]
        result["meta"].update(
            symbol=ticker, instrumentType="ETF" if ticker in ("SPY", "QQQ") else "EQUITY"
        )
        if ticker in ("SPY", "QQQ"):
            result["indicators"]["adjclose"][0]["adjclose"] = [50, 60, 70]
        return httpx.Response(200, json=data)

    service.market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW
    )
    service.discovery.prepare_valuation = lambda ticker, day: {"checked": True}
    return service


def request(tickers=("AAPL",), **kwargs):
    return ComparisonRequest(
        tickers=list(tickers), benchmark="SPY", as_of=date(2026, 10, 8), **kwargs
    )


def result(answer):
    return answer.trace[0]["result"]


def test_single_company_has_separate_etf_evidence_and_same_window_difference(service):
    prepared = []
    service.discovery.prepare_valuation = lambda ticker, day: (
        prepared.append(ticker) or {"checked": True}
    )
    answer = service.compare_companies(request())
    data = result(answer)
    assert data["schema_version"] == BENCHMARK_SCHEMA
    assert [r["ticker"] for r in data["rows"]] == ["AAPL"]
    assert [s["ticker"] for s in data["chart"]["series"]] == ["AAPL", "SPY"]
    assert Decimal(data["chart"]["relative"][0]["difference_pp"]) == -20
    assert prepared == ["AAPL"], "benchmark must not download company filings"
    assert answer.usage["model_calls"] == 0
    report = audit_package(
        service.export_package(answer.id), corpus=service.corpus, market_cache=service.market.cache
    )
    assert report["status"] == "consistent", report
    assert report["market_sources"]["checked"] == 2


def test_four_companies_and_benchmark_have_five_curves_without_exceeding_claim_limit(service):
    answer = service.compare_companies(request(["AAPL", "MSFT", "AMZN", "NVDA"]))
    assert len(result(answer)["rows"]) == 4 and len(result(answer)["chart"]["series"]) == 5
    assert len(result(answer)["chart"]["relative"]) == 4 and len(answer.answer) <= 8
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_relative_change_uses_only_shared_observations_and_unconverted_currency(service):
    history = service.market.history

    def shorter(ticker, *args, **kwargs):
        snapshot = history(ticker, *args, **kwargs)
        if ticker == "SPY":
            return snapshot.model_copy(update={"points": snapshot.points[1:]})
        return snapshot.model_copy(update={"currency": "HKD"})

    service.market.history = shorter
    answer = service.compare_companies(request(["0700.HK"], refresh_sources=False))
    chart = result(answer)["chart"]
    assert chart["common_days"] == 2 and chart["start"] == "2026-10-06"
    assert [s["currency"] for s in chart["series"]] == ["HKD", "USD"]
    assert Decimal(chart["relative"][0]["difference_pp"]) == Decimal(
        chart["series"][0]["change_pct"]
    ) - Decimal(chart["series"][1]["change_pct"])
    assert Decimal(chart["series"][0]["change_pct"]) == 50


def test_unadjusted_benchmark_forces_same_basis_for_relative_difference(service):
    history = service.market.history

    def no_adjustment(ticker, *args, **kwargs):
        snapshot = history(ticker, *args, **kwargs)
        if ticker == "SPY":
            return snapshot.model_copy(
                update={
                    "basis": "close",
                    "points": [p.model_copy(update={"adjusted": None}) for p in snapshot.points],
                }
            )
        return snapshot

    service.market.history = no_adjustment
    answer = service.compare_companies(request(refresh_sources=False))
    chart = result(answer)["chart"]
    assert chart["basis"] == "close" and chart["warning"]
    assert Decimal(chart["relative"][0]["difference_pp"]) == 0


def test_stale_benchmark_keeps_old_date_and_visible_gap(service):
    original = service.market.client
    saved = service.market.history("SPY", "5Y", date(2026, 10, 8))
    service.market.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(503) if req.url.path.endswith("/SPY") else original.send(req)
        )
    )
    answer = service.compare_companies(request(refresh_sources=False, refresh_prices=True))
    assert answer.status == "partial" and result(answer)["benchmark"]["issues"]
    benchmark_id = result(answer)["benchmark"]["market_id"]
    snapshot = next(e["snapshot"] for e in answer.evidence if e["id"] == benchmark_id)
    assert snapshot["stale"] and snapshot["retrieved_at"] == saved.retrieved_at.isoformat().replace(
        "+00:00", "Z"
    )
    report = audit_package(
        service.export_package(answer.id), corpus=service.corpus, market_cache=service.market.cache
    )
    assert report["status"] == "consistent", report


@pytest.mark.parametrize(
    "payload",
    [
        {"tickers": ["AAPL"]},
        {"tickers": ["AAPL"], "benchmark": "^GSPC"},
        {"tickers": ["SPY"], "benchmark": "SPY"},
        {"tickers": ["QQQ", "AAPL"], "benchmark": "QQQ"},
        {"tickers": ["AAPL"] * 5, "benchmark": "SPY"},
    ],
)
def test_invalid_single_duplicate_or_unsupported_benchmark_is_rejected(payload):
    with pytest.raises(ValueError):
        ComparisonRequest(**payload)


def test_benchmark_failure_retains_two_company_chart_and_sanitizes_error(service):
    history = service.market.history

    def failure(ticker, *args, **kwargs):
        if ticker == "SPY":
            raise httpx.ConnectError("private-source?api_key=hidden")
        return history(ticker, *args, **kwargs)

    service.market.history = failure
    answer = service.compare_companies(request(["AAPL", "MSFT"], refresh_sources=False))
    data = result(answer)
    assert data["chart"]["found"] and len(data["chart"]["series"]) == 2
    assert "relative" not in data["chart"] and data["benchmark"]["market_id"] is None
    assert data["benchmark"]["issues"] and answer.status == "partial"
    assert (
        "private-source" not in answer.model_dump_json()
        and "hidden" not in answer.model_dump_json()
    )
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize("wrong", ["type", "currency"])
def test_wrong_benchmark_identity_never_replaces_valid_company_data(service, wrong):
    history = service.market.history

    def malformed(ticker, *args, **kwargs):
        snapshot = history(ticker, *args, **kwargs)
        if ticker == "SPY":
            snapshot = snapshot.model_copy(
                update={"instrument_type": "INDEX"} if wrong == "type" else {"currency": "GBP"}
            )
        return snapshot

    service.market.history = malformed
    answer = service.compare_companies(request(refresh_sources=False))
    assert result(answer)["rows"][0]["market_id"]
    assert not result(answer)["benchmark"]["market_id"] and not result(answer)["chart"]["found"]
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize(
    "tamper", ["delta", "ticker", "definition", "source", "type", "currency", "cutoff", "v1_extra"]
)
def test_resealed_benchmark_tampering_is_rejected(service, tamper):
    answer = service.compare_companies(request(refresh_sources=False))
    package = copy.deepcopy(service.export_package(answer.id))
    data = package["research"]["trace"][0]["result"]
    benchmark = data["benchmark"]
    evidence = next(e for e in package["research"]["evidence"] if e["id"] == benchmark["market_id"])
    if tamper == "delta":
        data["chart"]["relative"][0]["difference_pp"] = "20"
    elif tamper == "ticker":
        benchmark["ticker"] = "QQQ"
    elif tamper == "definition":
        benchmark["name"] = "S&P 500 index"
    elif tamper == "source":
        benchmark["market_id"] = data["rows"][0]["market_id"]
    elif tamper == "type":
        evidence["snapshot"]["instrument_type"] = "INDEX"
    elif tamper == "currency":
        evidence["snapshot"]["currency"] = "GBP"
    elif tamper == "cutoff":
        evidence["snapshot"]["as_of"] = "2026-10-07"
    else:
        from filingcopilot.comparison import NOTE, SCHEMA

        data.update(schema_version=SCHEMA, note=NOTE)
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


@pytest.mark.parametrize(
    "question,benchmark",
    [
        ("亚马逊近五年跑赢大盘了吗？", "SPY"),
        ("近一年股价与 QQQ 比较", "QQQ"),
        ("全部历史股价与标普500基准对比", "SPY"),
        ("营收为何超过纳斯达克行业企业", None),
        ("股价与恒生大盘对比", None),
        ("SPY 和 QQQ 股价对比", None),
        ("未来股价能跑赢大盘吗", None),
    ],
)
def test_only_supported_plain_benchmark_questions_route_directly(question, benchmark):
    assert question_benchmark(question) == benchmark


def test_free_question_and_api_save_zero_model_package_without_filings(service):
    service.discovery.prepare = lambda *a, **k: pytest.fail(
        "price comparison must not acquire filings"
    )
    service.discovery.prepare_valuation = lambda *a, **k: pytest.fail("no source refresh requested")
    query = ResearchRequest(
        question="近五年股价与 SPY 基准比较",
        ticker="AAPL",
        as_of=date(2026, 10, 8),
        auto_acquire=True,
    )
    plan = service.research_plan(query)
    assert plan["ready"] and plan["kind"] == "benchmark" and not plan["uses_model"]
    answer = service.research(query)
    assert answer.mode == "comparison" and answer.question == query.question and answer.answer
    assert answer.usage["model_calls"] == 0 and all(c["kind"] == "market" for c in answer.answer)
    assert (
        audit_package(
            service.export_package(answer.id),
            corpus=service.corpus,
            market_cache=service.market.cache,
        )["status"]
        == "consistent"
    )
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/comparison", json=request(refresh_sources=False).model_dump(mode="json")
        )
        assert response.status_code == 200
        assert response.json()["trace"][0]["result"]["benchmark"]["ticker"] == "SPY"


def test_preflight_limits_period_and_company_scope_without_blocking_price_only_listing(service):
    for question in ("近三年股价与 SPY 比较", "苹果与微软近五年股价和 SPY 比较"):
        assert not service.research_plan(ResearchRequest(question=question, ticker="AAPL"))["ready"]
    assert service.research_plan(
        ResearchRequest(question="近一年股价与 SPY 比较", ticker="0700.HK")
    )["ready"]
    assert any("SPY" in q for q in service.capabilities("0700.HK")["questions"])


def test_price_workers_inherit_cooperative_research_control(service):
    control = ResearchControl()
    history = service.market.history
    observed = []

    def checked(ticker, *args, **kwargs):
        observed.append(current_control())
        checkpoint()
        return history(ticker, *args, **kwargs)

    service.market.history = checked
    answer = service.research(
        ResearchRequest(question="近一年股价与 SPY 比较", ticker="AAPL", as_of=date(2026, 10, 8)),
        control=control,
    )
    assert observed and all(c is control for c in observed)
    assert answer.mode == "comparison"


@pytest.mark.parametrize("ticker", ["AAPL", "0700.HK"])
def test_unavailable_index_preflight_offers_supported_etfs_instead_of_ignoring_question(
    service, ticker
):
    plan = service.research_plan(
        ResearchRequest(question="近五年股价与恒生大盘对比", ticker=ticker)
    )
    assert not plan["ready"] and plan["kind"] == "benchmark" and not plan["uses_model"]
    assert "尚未接入" in plan["message"] and len(plan["suggestions"]) == 2
    assert all(
        service.research_plan(ResearchRequest(question=q, ticker=ticker))["ready"]
        for q in plan["suggestions"]
    )
