import copy
import json
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from test_valuation import DAY

from filingcopilot import cli
from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, digest
from filingcopilot.comparison import VALUATION_SCHEMA, ComparisonRequest, validate_comparison
from filingcopilot.market import MarketData
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

NOW = datetime.combine(DAY, time(10), ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def provider_service(tmp_path, fixed_fixture_date):
    fixed_fixture_date(DAY)
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    controls = {"calls": [], "failed": None, "value": {}, "currency": {}, "stale": False}

    def handle(request):
        ticker = request.url.path.rsplit("/", 1)[-1]
        if "fundamentals-timeseries" in str(request.url):
            controls["calls"].append(ticker)
            if controls["failed"] == ticker:
                raise httpx.ReadTimeout("private-request-header", request=request)
            return httpx.Response(
                200,
                json={
                    "timeseries": {
                        "error": None,
                        "result": [
                            {
                                "meta": {"symbol": [ticker], "type": ["trailingDilutedEPS"]},
                                "trailingDilutedEPS": [
                                    {
                                        "asOfDate": (DAY - timedelta(days=60)).isoformat(),
                                        "periodType": "TTM",
                                        "currencyCode": controls["currency"].get(ticker, "USD"),
                                        "reportedValue": {"raw": controls["value"].get(ticker, 4)},
                                    }
                                ],
                            }
                        ],
                    }
                },
            )
        stamps = [int((NOW - timedelta(days=d)).timestamp()) for d in (3, 2, 1)]
        return httpx.Response(
            200,
            json={
                "chart": {
                    "error": None,
                    "result": [
                        {
                            "meta": {
                                "symbol": ticker,
                                "currency": "USD",
                                "instrumentType": "ETF" if ticker == "SPY" else "EQUITY",
                                "longName": ticker,
                                "exchangeTimezoneName": "America/New_York",
                                "regularMarketPrice": 100,
                                "regularMarketTime": int((NOW - timedelta(hours=1)).timestamp()),
                            },
                            "timestamp": stamps,
                            "indicators": {
                                "quote": [{"close": [90, 95, 99]}],
                                "adjclose": [{"adjclose": [90, 95, 99]}],
                            },
                        }
                    ],
                }
            },
        )

    service.market = MarketData(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(handle)), now=lambda: NOW
    )
    yield service, controls
    service.discovery.executor.shutdown(wait=True)


def run(service, **kwargs):
    return service.compare_companies(
        ComparisonRequest(
            tickers=["AAPL", "MSFT"],
            as_of=DAY,
            refresh_sources=False,
            include_ttm_valuation=True,
            **kwargs,
        )
    )


def data(answer):
    return answer.trace[0]["result"]


def test_optional_ttm_has_separate_eps_and_quote_bound_pe(provider_service):
    service, controls = provider_service
    controls["value"]["MSFT"] = 8
    answer = run(service)
    assert data(answer)["schema_version"] == VALUATION_SCHEMA
    assert answer.usage["model_calls"] == 0
    assert not any("claim " in gap or "Requested metric missing" in gap for gap in answer.gaps)
    assert len(answer.answer) == 4
    assert [item["kind"] for item in answer.answer] == ["market", "calculation"] * 2
    ledger = {e["id"]: e for e in answer.evidence}
    for row, multiple in zip(data(answer)["rows"], ("25", "12.5")):
        eps = ledger[row["cells"]["provider_ttm_eps"]]
        pe = ledger[row["cells"]["provider_ttm_pe"]]
        assert eps["kind"] == "valuation"
        assert pe["value"] == multiple
        assert pe["input_ids"] == [row["market_id"], eps["id"]]
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_default_comparison_keeps_old_schema_and_no_supplier_request(provider_service):
    service, controls = provider_service
    answer = service.compare_companies(
        ComparisonRequest(tickers=["AAPL", "MSFT"], as_of=DAY, refresh_sources=False)
    )
    assert data(answer)["schema_version"].endswith("/v1")
    assert "include_ttm_valuation" not in data(answer)
    assert controls["calls"] == []


def test_benchmark_is_not_requested_as_stock_eps(provider_service):
    service, controls = provider_service
    answer = run(service, benchmark="SPY")
    assert data(answer)["benchmark"]["ticker"] == "SPY"
    assert sorted(controls["calls"]) == ["AAPL", "MSFT"]
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_failed_eps_preserves_other_company_and_chart_without_private_error(provider_service):
    service, controls = provider_service
    controls["failed"] = "MSFT"
    answer = run(service)
    left, right = data(answer)["rows"]
    assert left["cells"]["provider_ttm_pe"]
    assert "provider_ttm_pe" not in right["cells"]
    assert data(answer)["chart"]["found"] and right["market_id"]
    assert any(i["scope"] == "valuation" for i in right["issues"])
    assert "private-request-header" not in answer.model_dump_json()
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize("value", [0, -1])
def test_loss_or_zero_eps_kept_without_positive_multiple(provider_service, value):
    service, controls = provider_service
    controls["value"]["MSFT"] = value
    answer = run(service)
    row = data(answer)["rows"][1]
    assert "provider_ttm_eps" in row["cells"] and "provider_ttm_pe" not in row["cells"]
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_currency_mismatch_keeps_supplier_currency_but_no_pe(provider_service):
    service, controls = provider_service
    controls["currency"]["MSFT"] = "EUR"
    answer = run(service)
    row = data(answer)["rows"][1]
    assert "provider_ttm_eps" in row["cells"] and "provider_ttm_pe" not in row["cells"]
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize("case", ["stale_eps", "stale_price", "split", "old_period"])
def test_unmatched_current_inputs_retain_eps_without_pe(provider_service, case):
    service, _ = provider_service
    original_eps = service.market.valuations.eps
    original_history = service.market.history

    def eps(ticker, as_of):
        snapshot = original_eps(ticker, as_of)
        if ticker == "MSFT" and case in ("stale_eps", "old_period"):
            update = (
                {"stale": True, "retrieved_at": NOW - timedelta(days=1)}
                if case == "stale_eps"
                else {"period_end_label": DAY - timedelta(days=201)}
            )
            return snapshot.model_copy(update=update)
        return snapshot

    def history(ticker, period, as_of, **kwargs):
        snapshot = original_history(ticker, period, as_of, **kwargs)
        if ticker == "MSFT" and case in ("stale_price", "split"):
            return snapshot.model_copy(
                update={"stale": True}
                if case == "stale_price"
                else {
                    "split_dates": [DAY - timedelta(days=30)],
                }
            )
        return snapshot

    service.market.valuations.eps = eps
    service.market.history = history
    answer = run(service)
    row = data(answer)["rows"][1]
    assert "provider_ttm_eps" in row["cells"] and "provider_ttm_pe" not in row["cells"]
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_registered_unmatched_share_class_does_not_request_provider_eps(provider_service):
    service, controls = provider_service
    answer = service.compare_companies(
        ComparisonRequest(
            tickers=["AAPL", "TSM"],
            as_of=DAY,
            refresh_sources=False,
            include_ttm_valuation=True,
        )
    )
    row = data(answer)["rows"][1]
    assert controls["calls"] == ["AAPL"]
    assert "provider_ttm_eps" not in row["cells"]
    assert any("股份类别" in i["message"] for i in row["issues"])
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_historical_request_keeps_prices_without_fetching_current_eps(provider_service):
    service, controls = provider_service
    answer = service.compare_companies(
        ComparisonRequest(
            tickers=["AAPL", "MSFT"],
            as_of=DAY - timedelta(days=2),
            refresh_sources=False,
            include_ttm_valuation=True,
        )
    )
    assert controls["calls"] == [] and data(answer)["chart"]["found"]
    assert all("provider_ttm_eps" not in r["cells"] for r in data(answer)["rows"])
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_ttm_can_exist_without_annual_basis(provider_service):
    service, _ = provider_service
    # Keep typed EPS independent from annual fact availability.
    original = service.tools

    def tools(day):
        result = original(day)
        result.get_company_overview = lambda ticker: {"found": False}
        return result

    service.tools = tools
    answer = run(service)
    assert all(not r["annual_basis_id"] for r in data(answer)["rows"])
    assert all(r["cells"]["provider_ttm_pe"] for r in data(answer)["rows"])
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


@pytest.mark.parametrize("tamper", ["eps", "pe", "schema", "missing", "price", "note"])
def test_resealed_ttm_comparison_tampering_is_rejected(provider_service, tamper):
    service, _ = provider_service
    answer = run(service)
    package = copy.deepcopy(service.export_package(answer.id))
    result = package["research"]["trace"][0]["result"]
    row, other = result["rows"]
    if tamper in ("eps", "pe"):
        key = "provider_ttm_" + tamper
        row["cells"][key] = other["cells"][key]
    elif tamper == "schema":
        result["include_ttm_valuation"] = False
    elif tamper == "missing":
        del row["cells"]["provider_ttm_pe"]
    elif tamper == "price":
        row["market_id"] = other["market_id"]
    else:
        result["note"] = "Current multiples prove cheapness."
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    assert audit_package(package)["status"] == "invalid"


def test_saved_v3_replays_after_calendar_moves_on(provider_service, monkeypatch):
    service, _ = provider_service
    answer = run(service)
    monkeypatch.setattr(service.market, "now", lambda: NOW + timedelta(days=30))
    ledger = {e["id"]: e for e in answer.evidence}
    validate_comparison(data(answer), ledger, service.domain, answer.as_of)


def test_api_optional_valuation_exports_and_audits(provider_service):
    service, _ = provider_service
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/comparison",
            json={
                "tickers": ["AAPL", "MSFT"],
                "as_of": DAY.isoformat(),
                "refresh_sources": False,
                "include_ttm_valuation": True,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["trace"][0]["result"]["schema_version"] == VALUATION_SCHEMA
        assert client.get(f"/api/runs/{result['id']}/audit").json()["status"] == "consistent"


@pytest.mark.parametrize("command", ["trend", "compare"])
def test_cli_flag_belongs_to_compare_and_does_not_break_trend(monkeypatch, capsys, command):
    captured = []

    def capture(request):
        captured.append(request)
        return SimpleNamespace(model_dump=lambda **kwargs: {"ok": True})

    monkeypatch.setattr(
        cli,
        "FilingCopilot",
        lambda settings: SimpleNamespace(
            compare_companies=capture,
            financial_trend=capture,
        ),
    )
    args = ["filingcopilot", command, "AAPL"]
    if command == "compare":
        args += ["MSFT", "--ttm-valuation"]
    args += ["--local-sources"]
    monkeypatch.setattr("sys.argv", args)
    cli.main()
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert (
        captured[0].include_ttm_valuation
        if command == "compare"
        else not hasattr(captured[0], "include_ttm_valuation")
    )
