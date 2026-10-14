import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, digest
from filingcopilot.ingestion import extract_passages
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import render_numeric

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.fixture
def service(tmp_path):
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))


def package_for(service, question="苹果 FY2025 营收同比和毛利率"):
    answer = service.research(
        ResearchRequest(language="zh", question=question, ticker="AAPL", as_of="2026-10-08", mode="reference")
    )
    return service.export_package(answer.id)


def reseal(package):
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    return package


def first_calculation(package):
    return next(e for e in package["research"]["evidence"] if e["kind"] == "calculation")


def test_package_recomputes_without_model_or_database(service):
    package = package_for(service)
    report = audit_package(package)
    assert report["status"] == "consistent"
    assert report["source_validation"] == "not_requested"
    assert report["numeric_claims_recomputed"] == len(package["research"]["answer"])
    assert any(c["code"] == "calculation" and c["status"] == "pass" for c in report["checks"])


def test_package_matches_independently_supplied_corpus(service):
    report = audit_package(
        package_for(service), corpus=service.corpus, trusted_domain=service.domain
    )
    assert report["status"] == "consistent" and report["source_validation"] == "matched"
    assert report["source_evidence_checked"] >= 3


def test_checksum_does_not_prove_source_even_when_every_field_is_rewritten(service):
    package = package_for(service, "苹果 FY2025 营收")
    fact = package["research"]["evidence"][0]
    fact["value"] = "100000000000"
    package["research"]["answer"][0]["text"] = render_numeric(fact, service.domain)
    reseal(package)
    assert audit_package(package)["status"] == "consistent"
    anchored = audit_package(package, corpus=service.corpus)
    assert anchored["status"] == "invalid" and anchored["source_validation"] == "mismatch"


@pytest.mark.parametrize(
    "change",
    [
        "text",
        "calculation",
        "formula",
        "input_id",
        "date",
        "company",
        "unit",
        "quarter",
        "missing",
        "duplicate",
        "index",
        "status",
    ],
)
def test_resealed_tampering_is_rejected(service, change):
    package = package_for(service)
    research = package["research"]
    calc = first_calculation(package)
    if change == "text":
        research["answer"][0]["text"] = "营收是 999 亿美元。"
    elif change == "calculation":
        calc["value"] = "999"
    elif change == "formula":
        calc["formula"] = "left + right"
    elif change == "input_id":
        calc["input_ids"][0] = "invented"
    elif change == "date":
        research["as_of"] = "2020-01-01"
    elif change == "company":
        research["question"] = "微软 FY2025 营收同比和毛利率"
    elif change == "unit":
        calc["inputs"][0]["unit"] = "EUR"
    elif change == "quarter":
        calc["inputs"][0]["start"] = "2025-07-01"
    elif change == "missing":
        research["evidence"] = []
    elif change == "duplicate":
        duplicate = deepcopy(research["evidence"][0])
        duplicate["value"] = "1"
        research["evidence"].append(duplicate)
    elif change == "index":
        research["answer"][1]["index"] = research["answer"][0]["index"]
    else:
        research["answer"] = []
        research["status"] = "answered"
    report = audit_package(reseal(package), corpus=service.corpus)
    assert report["status"] == "invalid" and report["checks_failed"]


def test_changed_content_without_updated_checksum_fails(service):
    package = package_for(service)
    package["research"]["question"] += "?"
    report = audit_package(package)
    assert any(c["code"] == "manifest" and c["status"] == "fail" for c in report["checks"])


@pytest.mark.parametrize("payload", [None, [], {}, {"schema_version": "v9000"}])
def test_malformed_package_returns_an_invalid_report(payload):
    assert audit_package(payload)["status"] == "invalid"


def test_model_review_marker_is_not_a_new_semantic_review(service):
    package = package_for(service, "苹果 FY2025 风险")
    claim = package["research"]["answer"][0]
    claim.update(kind="narrative", text="公司披露了相关风险。", verification="semantic_review")
    package["research"]["answer"] = [claim]
    report = audit_package(reseal(package), corpus=service.corpus)
    assert report["status"] == "consistent"
    assert report["narrative_claims_not_rechecked"] == 1
    assert any(
        c["code"] == "semantic_entailment" and c["status"] == "not_checked"
        for c in report["checks"]
    )


def test_modified_excerpt_cannot_pass_even_with_a_resealed_checksum(service):
    package = package_for(service, "苹果 FY2025 风险")
    package["research"]["answer"][0]["text"] = "This is an invented source excerpt."
    assert audit_package(reseal(package))["status"] == "invalid"


def test_export_keeps_configuration_at_research_time(service):
    package = package_for(service)
    service.domain = service.domain.model_copy(
        update={"currencies": {**service.domain.currencies, "USD": "测试币"}}
    )
    assert service.export_package(package["research"]["id"]) == package
    assert service.audit_run(package["research"]["id"])["status"] == "invalid"


def test_legacy_run_export_discloses_unknown_historical_configuration(service):
    request = ResearchRequest(language="zh", question="苹果 FY2025 营收", as_of="2026-10-08", mode="reference")
    answer = service.reference(request, service.tools(request.as_of))
    service.corpus.save_run(answer.model_dump(mode="json"))
    package = service.export_package(answer.id)
    assert package["domain_origin"] == "export_time_legacy"
    assert any(c["code"] == "legacy_configuration" for c in audit_package(package)["checks"])


def test_api_export_audit_and_versions(service):
    client = TestClient(create_app(service))
    package = package_for(service)
    run_id = package["research"]["id"]
    assert client.get(f"/api/runs/{run_id}/package").json() == package
    assert client.get(f"/api/runs/{run_id}/audit").json()["status"] == "consistent"
    assert client.get("/api/runs/missing/package").status_code == 404
    response = client.get(
        "/api/facts/versions?ticker=AAPL&metric=revenue&fiscal_year=2024&as_of=2026-10-08"
    )
    data = response.json()
    assert response.status_code == 200 and len(data["versions"]) >= 2
    assert data["selection"] == "latest_available"
    assert (
        client.get("/api/facts/versions?ticker=AAPL&metric=invalid&fiscal_year=2024").status_code
        == 422
    )


def test_cli_offline_audit_needs_no_home_or_model(service, tmp_path):
    target = tmp_path / "package.json"
    target.write_text(json.dumps(package_for(service)), encoding="utf-8")
    unavailable_home = tmp_path / "must-not-create"
    env = {
        **os.environ,
        "FC_HOME": str(unavailable_home),
        "FC_API_BASE": "http://127.0.0.1:1",
        "FC_API_KEY": "dummy",
        "FC_MODEL": "unavailable",
        "PYTHONUTF8": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "filingcopilot", "audit", str(target)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "consistent"
    assert not unavailable_home.exists()
    target.write_text("{broken", encoding="utf-8")
    broken = subprocess.run(
        [sys.executable, "-m", "filingcopilot", "audit", str(target)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert broken.returncode == 1 and json.loads(broken.stdout)["status"] == "invalid"


def test_raw_cache_validates_filing_bytes_xbrl_and_extracted_passages(tools, tmp_path):
    filing = tools.corpus.catalog(tools.as_of)[0]
    html = b"<html><body><p>The company relies on manufacturing partners. Supply disruptions could adversely affect operations. No default has occurred. This risk disclosure describes potential events rather than realized losses.</p></body></html>"
    filing["content_sha256"] = hashlib.sha256(html).hexdigest()
    from filingcopilot.models import Filing

    document = Filing.model_validate(filing)
    facts = tools.corpus.facts("AAPL", "revenue", 2025, tools.as_of)
    fact = facts[0].model_copy(
        update={
            "concept": "us-gaap:Test",
            "locator": f"facts.us-gaap.Test.units.USD[0]; accession={document.accession}",
        }
    )
    tools.corpus.ingest(document, [fact], extract_passages(html, document))
    cache = tmp_path / "sources"
    cache.mkdir()

    def write(url, body):
        (cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")).write_bytes(body)

    write(document.source_url, html)
    write(
        fact.source_url,
        json.dumps(
            {
                "facts": {
                    "us-gaap": {
                        "Test": {
                            "units": {
                                "USD": [
                                    {
                                        "accn": document.accession,
                                        "form": "10-K",
                                        "val": 200,
                                        "start": str(fact.start),
                                        "end": str(fact.end),
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        ).encode(),
    )
    service = FilingCopilot(Settings(home=tools.corpus.home, embedding_mode="off"))
    package = package_for(service, "苹果 FY2025 营收和供应风险")
    report = audit_package(package, source_cache=cache)
    assert report["status"] == "consistent"
    assert {"raw_filing_hash", "raw_xbrl_fact", "raw_passage"} <= {
        c["code"] for c in report["checks"]
    }
    write(document.source_url, html + b"tampered")
    assert audit_package(package, source_cache=cache)["status"] == "invalid"
    write(document.source_url, html)
    write(fact.source_url, b'{"facts":{}}')
    report = audit_package(package, source_cache=cache)
    assert any(c["code"] == "raw_xbrl_fact" and c["status"] == "fail" for c in report["checks"])


def test_missing_raw_cache_is_explicitly_unchecked(service, tmp_path):
    report = audit_package(
        package_for(service), corpus=service.corpus, source_cache=tmp_path / "empty-cache"
    )
    assert report["status"] == "consistent" and report["source_validation"] == "matched"
    assert report["raw_sources"]["missing"] > 0
    assert any(
        c["code"] == "raw_source_missing" and c["status"] == "not_checked" for c in report["checks"]
    )


def test_same_year_version_difference_requires_identical_periods(tools):
    original = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    tools.ledger["shifted"] = {**original, "id": "shifted", "start": "2024-10-02"}
    with pytest.raises(ValueError, match="exact same reporting period"):
        tools.calculate("difference", "shifted", original["id"])


def test_versions_do_not_leak_future_changes_or_claim_restatement_causes(tools):
    from filingcopilot.models import Filing

    original = tools.corpus.facts("AAPL", "revenue", 2025, tools.as_of)[0]
    filing = Filing.model_validate(tools.corpus.catalog(tools.as_of)[0]).model_copy(
        update={
            "id": "AAPL:new",
            "accession": "amended",
            "form": "10-K/A",
            "filed": date(2026, 11, 1),
        }
    )
    revised = original.model_copy(
        update={"id": "rev25-new", "filing_id": filing.id, "value": original.value + 1}
    )
    tools.corpus.ingest(filing, [revised], [])
    before = tools.get_fact_versions("AAPL", "revenue", 2025)
    assert len(before["versions"]) == 1 and before["selected_id"] == original.id
    tools.as_of = date(2026, 11, 1)
    after = tools.get_fact_versions("AAPL", "revenue", 2025)
    assert after["selected_id"] == revised.id
    assert after["versions"][0]["comparison"] == "changed_value"
    assert after["versions"][0]["role"] == "amendment"
    assert after["versions"][0]["previous_evidence_id"] == original.id
    comparison = tools.calculate("difference", revised.id, original.id)["evidence"]
    assert comparison["value"] == "1"
    assert comparison["label"].endswith("申报版本差额")
    assert comparison["formula"] == "left filing value - right filing value"


def test_version_conflicts_are_not_silently_selected(tools):
    from filingcopilot.models import Filing

    filing = Filing.model_validate(tools.corpus.catalog(tools.as_of)[0])
    original = tools.corpus.facts("AAPL", "revenue", 2025, tools.as_of)[0]
    conflicting = original.model_copy(update={"id": "conflict", "value": original.value + 1})
    tools.corpus.ingest(filing, [original, conflicting], [])
    result = tools.get_fact_versions("AAPL", "revenue", 2025)
    assert result["selection"] == "ambiguous" and result["selected_id"] is None
    with pytest.raises(ValueError, match="Conflicting"):
        tools.get_financial_fact("AAPL", "revenue", 2025)
