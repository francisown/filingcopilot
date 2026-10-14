import copy
import json
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from test_market import NOW, provider

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, digest
from filingcopilot.comparison import ComparisonRequest, rebased_chart, validate_comparison
from filingcopilot.market import MarketData, MarketError, market_evidence, parse_chart
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def snapshot(ticker, period="5Y", currency="USD", days=(5, 6, 7), adjusted=True):
    raw = provider()
    data = raw["chart"]["result"][0]
    data["meta"].update(symbol=ticker, currency=currency)
    indexes = [day - 5 for day in days]
    data["timestamp"] = [data["timestamp"][i] for i in indexes]
    for group in data["indicators"].values():
        for key, values in group[0].items():
            group[0][key] = [values[i] for i in indexes]
    if not adjusted:
        del data["indicators"]["adjclose"]
    return parse_chart(
        json.dumps(raw).encode(),
        ticker,
        period,
        date(2026, 10, 8),
        NOW,
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d",
    )


def chart_for(*snapshots):
    ledger = {e["id"]: e for e in map(market_evidence, snapshots)}
    return rebased_chart(list(ledger), ledger)


@pytest.fixture
def service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market.history = lambda ticker, period, as_of, **kwargs: snapshot(ticker, period)
    service.discovery.prepare_valuation = lambda ticker, day: {"checked": True}
    return service


def request(tickers=("AAPL", "MSFT"), **kwargs):
    return ComparisonRequest(tickers=list(tickers), as_of=date(2026, 10, 8), **kwargs)


def view(answer):
    return answer.trace[0]["result"]


def test_common_observed_dates_are_rebased_without_interpolation():
    result = chart_for(snapshot("AAPL"), snapshot("MSFT", days=(6, 7)))
    assert (result["start"], result["end"], result["common_days"]) == (
        "2026-10-06",
        "2026-10-07",
        2,
    )
    assert result["series"][0]["omitted_days"] == 1
    for series in result["series"]:
        assert [Decimal(p["value"]) for p in series["points"]] == [100, 150]
        assert Decimal(series["change_pct"]) == 50


@pytest.mark.parametrize("stale", [False, True])
def test_comparison_keeps_exchange_date_when_user_calendar_is_ahead(service, stale):
    now = datetime(2026, 10, 9, 1, tzinfo=timezone.utc)

    def handler(request):
        if stale:
            return httpx.Response(502)
        data = provider()
        data["chart"]["result"][0]["meta"]["symbol"] = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=data)

    service.market = MarketData(
        service.settings.home,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now,
    )
    if stale:
        for ticker in ("AAPL", "MSFT"):
            data = provider()
            data["chart"]["result"][0]["meta"]["symbol"] = ticker
            service.market._save(
                ticker,
                json.dumps(data).encode(),
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d",
                datetime(2026, 10, 7, 21, tzinfo=timezone.utc),
            )
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/comparison",
            json={"tickers": ["AAPL", "MSFT"], "as_of": "2026-10-09", "refresh_sources": False},
        )
        assert response.status_code == 200, response.text
    answer = service.corpus.run(response.json()["id"])
    assert answer["as_of"] == "2026-10-09"
    markets = [e["snapshot"] for e in answer["evidence"] if e["kind"] == "market"]
    assert all(m["as_of"] == ("2026-10-07" if stale else "2026-10-08") for m in markets)
    assert all(m["stale"] == stale for m in markets)
    package = service.export_package(answer["id"])
    assert audit_package(package, corpus=service.corpus)["status"] == "consistent"


@pytest.mark.parametrize("shift", [-1, 1])
def test_comparison_rejects_market_cutoff_other_than_observed_exchange_date(service, shift):
    answer = service.compare_companies(request(refresh_sources=False))
    ledger = {e["id"]: e for e in answer.evidence}
    market = next(e for e in ledger.values() if e["kind"] == "market")
    market["snapshot"]["as_of"] = (date(2026, 10, 8) + timedelta(days=shift)).isoformat()
    with pytest.raises(ValueError):
        validate_comparison(view(answer), ledger, service.domain, answer.as_of)


def test_cross_currency_series_remain_in_their_own_currency():
    result = chart_for(snapshot("AAPL"), snapshot("MSFT", currency="GBP"))
    assert [s["currency"] for s in result["series"]] == ["USD", "GBP"]
    assert all(Decimal(s["change_pct"]) == 20 for s in result["series"])


def test_mixed_adjustment_uses_close_consistently_and_discloses_limitation():
    result = chart_for(snapshot("AAPL"), snapshot("MSFT", adjusted=False))
    assert result["basis"] == "close" and "未复权" in result["warning"]
    assert [s["points"][0]["price"] for s in result["series"]] == ["100", "100"]


@pytest.mark.parametrize("days", [(5,), (7,)])
def test_insufficient_common_dates_do_not_fabricate_a_return(days):
    result = chart_for(snapshot("AAPL", days=(5,)), snapshot("MSFT", days=days))
    assert not result["found"] and not result["series"]


def test_one_market_source_is_not_a_company_comparison():
    assert not chart_for(snapshot("AAPL"))["found"]


@pytest.mark.parametrize(
    "tickers", [["AAPL"], ["AAPL"] * 5, ["aapl", "AAPL"], ["BRK.B", "BRK-B"], ["AAPL", "<script>"]]
)
def test_request_rejects_invalid_or_duplicate_listings(tickers):
    with pytest.raises(ValueError):
        request(tickers)


def test_request_normalizes_and_rejects_future():
    assert request([" aapl ", "brk.b"]).tickers == ["AAPL", "BRK-B"]
    with pytest.raises(ValueError):
        ComparisonRequest(tickers=["AAPL", "MSFT"], as_of=date(2099, 1, 1))


def test_comparison_has_explicit_financial_periods_zero_models_and_saved_package(service):
    answer = service.compare_companies(request())
    assert answer.usage["model_calls"] == 0 and answer.model is None
    result = view(answer)
    assert result["chart"]["found"] and len(result["rows"]) == 2
    ledger = {e["id"]: e for e in answer.evidence}
    periods = [ledger[r["annual_basis_id"]]["end"] for r in result["rows"]]
    assert periods[0] != periods[1], "different fiscal ends must remain explicit"
    assert all("operating_margin" in row["cells"] for row in result["rows"])
    assert service.corpus.run(answer.id)["mode"] == "comparison"
    report = audit_package(service.export_package(answer.id), corpus=service.corpus)
    assert report["status"] == "consistent", report
    assert any(
        c["code"] == "company_comparison" and c["status"] == "pass" for c in report["checks"]
    )


def test_four_companies_keep_existing_submission_limit(service):
    answer = service.compare_companies(request(["AAPL", "MSFT", "NVDA", "AMZN"]))
    assert len(view(answer)["rows"]) == 4 and len(answer.answer) <= 8
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_source_checks_run_independently_in_parallel(service, thread_timeout):
    barrier = threading.Barrier(2, timeout=thread_timeout)

    def prepare(ticker, day):
        barrier.wait()
        return {"checked": True}

    service.discovery.prepare_valuation = prepare
    answer = service.compare_companies(request())
    assert len(view(answer)["chart"]["series"]) == 2
    assert all(t["preparation"]["checked"] for t in answer.trace[1]["companies"])


def test_missing_market_retains_financials_and_other_company(service):
    def history(ticker, period, as_of, **kwargs):
        if ticker == "MSFT":
            raise MarketError("offline")
        return snapshot(ticker, period)

    service.market.history = history
    answer = service.compare_companies(request())
    rows = view(answer)["rows"]
    assert rows[0]["market_id"] and rows[1]["market_id"] is None
    assert rows[1]["cells"]["revenue"] and not view(answer)["chart"]["found"]
    assert answer.status == "partial"
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_failed_source_preparation_does_not_discard_prices_or_cached_facts(service):
    service.discovery.prepare_valuation = lambda *args: (_ for _ in ()).throw(
        httpx.ConnectError("secret-header-example")
    )
    answer = service.compare_companies(request())
    assert view(answer)["chart"]["found"] and all(r["cells"] for r in view(answer)["rows"])
    assert all(any(i["scope"] == "refresh" for i in r["issues"]) for r in view(answer)["rows"])
    assert "secret-header-example" not in answer.model_dump_json()


def test_no_sources_requested_does_not_prepare_or_use_a_model(service):
    service.discovery.prepare_valuation = lambda *args: pytest.fail("No source refresh expected")
    assert service.compare_companies(request(refresh_sources=False)).usage["model_calls"] == 0


def test_price_only_market_explains_coverage_without_futile_annual_retry(service):
    service.market.filing_scope = lambda ticker: False
    answer = service.compare_companies(request(["0700.HK", "MSFT"], refresh_sources=False))
    row = view(answer)["rows"][0]
    assert row["market_id"] and not row["annual_basis_id"]
    assert "尚未接入" in row["issues"][0]["message"]
    assert "重试自动准备" not in row["issues"][0]["message"]


@pytest.mark.parametrize("tamper", ["chart", "cell", "scope", "period"])
def test_resealed_comparison_tampering_is_rejected(service, tamper):
    answer = service.compare_companies(request())
    package = copy.deepcopy(service.export_package(answer.id))
    result = package["research"]["trace"][0]["result"]
    if tamper == "chart":
        result["chart"]["series"][0]["points"][1]["value"] = "1000"
    elif tamper == "cell":
        result["rows"][0]["cells"]["net_income"] = result["rows"][0]["cells"]["revenue"]
    elif tamper == "scope":
        result["rows"][0]["ticker"] = "MSFT"
    else:
        result["period"] = "1Y"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    report = audit_package(package)
    assert report["status"] == "invalid"
    assert any(
        c["code"] == "company_comparison" and c["status"] == "fail" for c in report["checks"]
    )


def test_api_validates_input_and_exports_replayable_result(service):
    with TestClient(create_app(service)) as client:
        assert client.post("/api/comparison", json={"tickers": ["AAPL"]}).status_code == 422
        response = client.post("/api/comparison", json=request().model_dump(mode="json"))
        assert response.status_code == 200
        record = response.json()
        assert client.get(f"/api/runs/{record['id']}/audit").json()["status"] == "consistent"
