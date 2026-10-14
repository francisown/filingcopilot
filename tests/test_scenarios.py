import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from test_valuation import NOW, eps_body, price_body

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, digest
from filingcopilot.market import MarketData
from filingcopilot.scenarios import ScenarioRequest, scenario_result, validate_scenarios
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def fixture_clock(fixed_fixture_date):
    fixed_fixture_date(NOW.date())


def request_data(basis="provider_ttm"):
    return {
        "ticker": "AAPL",
        "as_of": date.today().isoformat(),
        "basis": basis,
        "years": 3,
        "discount_pct": "10",
        "cases": [
            {"name": "low", "growth_pct": "0", "terminal_pe": "10"},
            {"name": "high", "growth_pct": "10", "terminal_pe": "20"},
        ],
    }


@pytest.fixture
def service(tmp_path):
    s = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    s.market = MarketData(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json=eps_body() if "fundamentals-timeseries" in str(r.url) else price_body(),
                )
            )
        ),
        now=lambda: NOW,
    )
    return s


def test_scenario_math_and_frozen_audit(service):
    answer = service.valuation_scenarios(ScenarioRequest.model_validate(request_data()))
    frozen = answer.trace[0]["result"]
    assert Decimal(frozen["rows"][1]["discounted_value"]) == Decimal("80")
    assert Decimal(frozen["rows"][1]["price_difference_pct"]) == Decimal("-20")
    reference = frozen["price_reference"]
    assert reference["period"] == "5Y"
    assert reference["low"] == "99" and reference["high"] == "99"
    assert "不是历史市盈率" in reference["note"]
    assert answer.usage["model_calls"] == 0
    assert service.audit_run(answer.id)["status"] == "consistent"
    bad = copy.deepcopy(service.export_package(answer.id))
    bad["research"]["trace"][0]["result"]["rows"][1]["discounted_value"] = "999"
    bad["manifest"]["payload_sha256"] = digest({k: v for k, v in bad.items() if k != "manifest"})
    assert audit_package(bad)["status"] == "invalid"


@pytest.mark.parametrize("retain_reference", [False, True])
def test_original_scenario_shape_replays_without_mutating_saved_package(service, retain_reference):
    answer = service.valuation_scenarios(ScenarioRequest.model_validate(request_data()))
    package = service.export_package(answer.id)
    frozen = package["research"]["trace"][0]["result"]
    if not retain_reference:
        del frozen["price_reference"]
    del frozen["schema_version"]
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    original = copy.deepcopy(package)
    assert audit_package(package)["status"] == "consistent"
    assert package == original


@pytest.mark.parametrize("change", ["missing", "value", "basis", "scope", "unknown_schema"])
def test_historical_price_reference_requires_versioned_source_replay(service, change):
    answer = service.valuation_scenarios(ScenarioRequest.model_validate(request_data()))
    bad = copy.deepcopy(service.export_package(answer.id))
    frozen = bad["research"]["trace"][0]["result"]
    if change == "missing":
        del frozen["price_reference"]
    elif change == "value":
        frozen["price_reference"]["median"] = "999"
    elif change == "basis":
        frozen["price_reference"]["basis"] = "close"
    elif change == "scope":
        frozen["price_reference"]["start"] = "2020-01-01"
    else:
        frozen["schema_version"] = "filingcopilot.valuation-scenarios/v99"
    bad["manifest"]["payload_sha256"] = digest({k: v for k, v in bad.items() if k != "manifest"})
    assert audit_package(bad)["status"] == "invalid"


def test_api_rejects_impossible_assumptions_and_preserves_sources(service):
    with TestClient(create_app(service)) as client:
        data = request_data()
        data["cases"][0]["growth_pct"] = "NaN"
        assert client.post("/api/valuation/scenarios", json=data).status_code == 422
        data = request_data()
        response = client.post("/api/valuation/scenarios", json=data)
        assert response.status_code == 200
        assert response.json()["mode"] == "scenarios"


@pytest.mark.parametrize(
    "change", ["company", "currency", "stale", "negative", "duplicate", "knowledge"]
)
def test_scenario_inputs_cannot_cross_source_boundaries(service, change):
    answer = service.valuation_scenarios(ScenarioRequest.model_validate(request_data()))
    frozen = copy.deepcopy(answer.trace[0]["result"])
    ledger = {e["id"]: copy.deepcopy(e) for e in answer.evidence}
    eps, market = ledger[frozen["eps_id"]], ledger[frozen["market_id"]]
    if change == "company":
        eps["ticker"] = "MSFT"
    if change == "currency":
        eps["snapshot"]["currency"] = "EUR"
    if change == "stale":
        market["snapshot"]["stale"] = True
    if change == "negative":
        eps["snapshot"]["value"] = "-2"
    if change == "duplicate":
        frozen["request"]["cases"][1]["name"] = frozen["request"]["cases"][0]["name"]
    if change == "knowledge":
        frozen["request"]["as_of"] = "2025-01-01"
    with pytest.raises(ValueError):
        validate_scenarios(frozen, ledger, answer.as_of)


def test_scenarios_do_not_mix_daily_backup_with_supplier_eps(service):
    answer = service.valuation_scenarios(ScenarioRequest.model_validate(request_data()))
    frozen = answer.trace[0]["result"]
    ledger = {e["id"]: copy.deepcopy(e) for e in answer.evidence}
    market = ledger[frozen["market_id"]]
    market["snapshot"]["quote_type"] = "historical_close"
    with pytest.raises(ValueError):
        scenario_result(
            ScenarioRequest.model_validate(request_data()), ledger[frozen["eps_id"]], market
        )
