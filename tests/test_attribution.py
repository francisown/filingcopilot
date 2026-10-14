import json
import os
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal, localcontext

import httpx
import pytest
from fastapi.testclient import TestClient
from test_valuation import NOW, eps_body, price_body

from filingcopilot.api import create_app
from filingcopilot.attribution import audit_attribution, build_attribution
from filingcopilot.audit import audit_package, digest
from filingcopilot.market import MarketData
from filingcopilot.models import ResearchRequest
from filingcopilot.scenarios import ScenarioRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import render_numeric

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.fixture
def service(tmp_path):
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))


def reseal(package):
    package["manifest"]["payload_sha256"] = digest({k: v for k, v in package.items() if k != "manifest"})
    return package


def package(service, question="苹果 FY2025 营收"):
    answer = service.research(ResearchRequest(language="zh", question=question, ticker="AAPL", mode="reference", as_of="2026-10-08"))
    return service.export_package(answer.id)


def test_numeric_versions_and_multiple_observations_are_not_collapsed(service):
    before = package(service)
    after = deepcopy(before)
    fact = after["research"]["evidence"][0]
    fact["id"] += "-amended"
    fact["filing"]["id"] += "-amended"
    fact["filing_id"] = fact["filing"]["id"]
    fact["filing"]["accession"] += "-amended"
    fact["filing"]["content_sha256"] = "a" * 64
    fact["value"] = str(Decimal(fact["value"]) + 25)
    after["research"]["answer"][0]["evidence_ids"] = [fact["id"]]
    after["research"]["answer"][0]["text"] = render_numeric(fact, service.domain)
    report = build_attribution(before, reseal(after))
    assert report["numeric_changes"][0]["delta"] == "25"
    assert report["numeric_changes"][0]["after"][0]["accession"].endswith("amended")
    assert audit_package(report)["status"] == "consistent"
    assert audit_package(report, corpus=service.corpus)["status"] == "invalid"
    # A package retaining both versions must not silently choose the last one.
    after["research"]["evidence"].append(deepcopy(before["research"]["evidence"][0]))
    report = build_attribution(before, reseal(after))
    assert len(report["numeric_changes"][0]["after"]) == 2
    assert "delta" not in report["numeric_changes"][0]


def test_unchanged_evidence_changed_narrative_requires_review(service):
    before = package(service, "苹果 FY2025 风险")
    before["research"]["answer"][0].update(kind="narrative", text="公司披露了供应链风险。", verification="semantic_review")
    reseal(before)
    after = deepcopy(before)
    after["research"]["answer"][0]["text"] = "公司经营仍受供应链约束。"
    report = build_attribution(before, reseal(after))
    assert report["narrative_changes"][0]["status"] == "needs_review"
    assert report["narrative_changes"][0]["evidence_versions_changed"] is False
    assert audit_package(report, corpus=service.corpus, trusted_domain=service.domain)["status"] == "consistent"
    assert report["before_package"] == before
    assert report["after_package"] == after


def scenario_package(service, *, growth="10", pe="20", discount="10", years=3, eps=4):
    # Real service path with controlled provider responses and immutable inputs.
    service.market = MarketData(
        service.settings.home / ("scenario-" + str(eps)),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json=eps_body(eps) if "fundamentals-timeseries" in str(r.url) else price_body()
        ))), now=lambda: NOW,
    )
    request = ScenarioRequest(language="zh", ticker="AAPL", basis="provider_ttm", as_of=NOW.date(), years=years,
                              discount_pct=discount, cases=[
                                  {"name": "low", "growth_pct": "0", "terminal_pe": "10"},
                                  {"name": "base", "growth_pct": growth, "terminal_pe": pe},
                              ])
    answer = service.valuation_scenarios(request)
    return service.export_package(answer.id)


@pytest.mark.parametrize("factor,new_value", [("growth", "20"), ("pe", "30"), ("discount", "5"), ("years", 5)])
def test_single_assumption_change_matches_hand_calculation(service, factor, new_value):
    before = scenario_package(service)
    after = scenario_package(service, **{factor: new_value})
    report = build_attribution(before, after)
    case = next(c for c in report["valuation"]["cases"] if c["name"] == "base")
    with localcontext() as ctx:
        ctx.prec = 40
        growth, pe, discount, years = Decimal(10), Decimal(20), Decimal(10), 3
        if factor == "growth":
            growth = Decimal(new_value)
        elif factor == "pe":
            pe = Decimal(new_value)
        elif factor == "discount":
            discount = Decimal(new_value)
        else:
            years = new_value
        expected = Decimal(4) * (1 + growth / 100) ** years * pe / (1 + discount / 100) ** years
        assert Decimal(case["assumption_changes"][0]["contribution"]) == expected - 80
        assert Decimal(case["residual"]) == 0
    assert audit_package(report)["status"] == "consistent"


def test_multi_factor_interactions_and_eps_changes_stay_in_residual(service):
    before = scenario_package(service)
    after = scenario_package(service, growth="20", pe="30", eps=6)
    report = build_attribution(before, after)
    case = next(c for c in report["valuation"]["cases"] if c["name"] == "base")
    growth, multiple = case["assumption_changes"]
    assert growth["assumptions"]["terminal_pe"] == "20"
    assert multiple["assumptions"]["growth_pct"] == "10"
    assert case["base_eps_changed"] is True
    assert Decimal(case["residual"]) != 0
    with localcontext() as ctx:
        ctx.prec = 40
        assert Decimal(case["total_change"]) == sum((Decimal(c["contribution"]) for c in case["assumption_changes"]), Decimal(0)) + Decimal(case["residual"])
    assert audit_attribution(report)["status"] == "consistent"


def test_resealed_changed_contribution_or_premise_is_rejected(service):
    before, after = scenario_package(service), scenario_package(service, growth="20")
    report = build_attribution(before, after)
    changed = deepcopy(report)
    case = next(c for c in changed["valuation"]["cases"] if c["name"] == "base")
    case["assumption_changes"][0]["contribution"] = "999"
    assert audit_package(reseal(changed))["status"] == "invalid"
    changed = deepcopy(report)
    changed["before_package"]["research"]["trace"][0]["result"]["base_eps"] = "999"
    reseal(changed["before_package"])
    assert audit_package(reseal(changed))["status"] == "invalid"


def test_scope_and_reversed_dates_cannot_be_compared(service):
    before = package(service)
    after = deepcopy(before)
    after["focus_ticker"] = "MSFT"
    with pytest.raises(ValueError):
        build_attribution(before, reseal(after))
    before["research"]["as_of"] = "2026-10-09"
    with pytest.raises(ValueError, match="知悉日期"):
        build_attribution(reseal(before), package(service))


def test_api_export_and_cli_audit_work_offline(service, tmp_path):
    before, after = package(service), package(service)
    with TestClient(create_app(service)) as client:
        response = client.get(f"/api/runs/{after['research']['id']}/attribution", params={"before_run_id": before["research"]["id"]})
        assert response.status_code == 200
        report = response.json()
    first, second, output = tmp_path / "before.json", tmp_path / "after.json", tmp_path / "report.json"
    first.write_text(json.dumps(before), "utf-8")
    second.write_text(json.dumps(after), "utf-8")
    env = {**os.environ, "FC_HOME": str(tmp_path / "must-not-create"), "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-m", "filingcopilot", "attribution", str(first), str(second), "--output", str(output)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text("utf-8")) == report
    result = subprocess.run([sys.executable, "-m", "filingcopilot", "audit", str(output)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "consistent"
    assert not (tmp_path / "must-not-create").exists()
