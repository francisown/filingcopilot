from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.company_page import build_company_page
from filingcopilot.execution import ResearchCancelled, ResearchControl, research_scope
from filingcopilot.models import Fact, Filing, Passage, StatementFact
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def service_at(tmp_path):
    return FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))


@pytest.mark.parametrize('key', ['ownership','audit'])
def test_share_and_audit_sections_refresh_annual_and_preserve_scope_note(tmp_path,monkeypatch,key):
    service = service_at(tmp_path)
    calls = []
    monkeypatch.setattr(service.discovery,'refresh_annual',lambda *a: calls.append('annual') or {'checked':True})
    monkeypatch.setattr(service.discovery,'prepare_quarterly',lambda *a: pytest.fail('Annual source block must not fetch quarterly data'))
    monkeypatch.setattr(service.market,'history',lambda *a,**k: pytest.fail('Source block must not fetch quotes'))
    page = build_company_page(service,'AAPL',date(2026,10,8),refresh=True,section=key,include_market=False)
    assert calls==['annual']
    assert page['sections'][0]['key']==key
    assert page['sections'][0]['note']
    assert 'ADS' in page['sections'][0]['note'] if key=='ownership' else '不是自动审计结论' in page['sections'][0]['note']


def test_company_page_refresh_prepares_sources_but_never_fetches_quotes_when_excluded(
    tmp_path, monkeypatch
):
    service = service_at(tmp_path)
    calls = []
    for method in (
        "refresh_annual",
        "prepare_quarterly",
        "prepare_updates",
        "prepare_external_updates",
    ):
        monkeypatch.setattr(
            service.discovery,
            method,
            lambda *args, name=method: calls.append(name) or {"checked": True},
        )
    monkeypatch.setattr(
        service.market,
        "history",
        lambda *args, **kwargs: pytest.fail("Company sources must not fetch prices"),
    )
    page = build_company_page(
        service, "AAPL", date(2026, 10, 8), refresh=True, include_market=False
    )
    assert set(calls) >= {"refresh_annual", "prepare_quarterly", "prepare_updates"}
    assert page["source_preparation"]
    assert page["annual_overview"]["found"]
    assert page["analysis_generated"] is False
    assert not any(e["kind"] == "market" for e in page["evidence"])


def test_company_page_section_retry_refreshes_only_its_source_and_keeps_raw_material(
    tmp_path, monkeypatch
):
    service = service_at(tmp_path)
    calls = []

    def unavailable(*args):
        calls.append("annual")
        raise ConnectionError("private transport details")

    monkeypatch.setattr(service.discovery, "refresh_annual", unavailable)
    monkeypatch.setattr(
        service.discovery, "prepare_updates", lambda *args: pytest.fail("Unrelated source")
    )
    page = build_company_page(
        service, "AAPL", date(2026, 10, 8), refresh=True, section="business", include_market=False
    )
    assert calls == ["annual"]
    assert len(page["sections"]) == 1
    section = page["sections"][0]
    assert section["evidence_ids"]
    assert section["status"] == "partial"
    assert "ConnectionError" in section["gap"]
    assert "private" not in section["gap"]


def test_company_business_sources_use_latest_public_annual_chapter_and_deduplicate(tmp_path):
    service = service_at(tmp_path)
    filing = Filing(
        id="AAPL:latest",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-26-000099",
        form="10-K",
        filed="2026-09-01",
        period_end="2026-06-30",
        source_url="https://www.sec.gov/latest.htm",
        content_sha256="c" * 64,
    )
    common = dict(
        filing_id=filing.id, ticker="AAPL", source_url=filing.source_url, locator="paragraph"
    )
    passages = [
        Passage(
            id="business1",
            section="Item 1. Business",
            text="We deliver products and services to business customers.",
            **common,
        ),
        Passage(
            id="business2",
            section="Item 1. Business",
            text="We deliver products and services to business customers.",
            **common,
        ),
        Passage(
            id="risk-high-match",
            section="Item 1A. Risk Factors",
            text="Business products services segments customers may pose risks.",
            **common,
        ),
    ]
    service.corpus.ingest(filing, [], passages)
    page = build_company_page(
        service, "AAPL", date(2026, 10, 8), section="business", include_market=False
    )
    assert len(page["sections"][0]["evidence_ids"]) == 1
    assert page["sections"][0]["evidence_ids"][0] in {"business1", "business2"}
    cited = next(e for e in page["evidence"] if e["id"] == page["sections"][0]["evidence_ids"][0])
    assert cited["filing"]["filed"] == "2026-09-01"
    assert cited["section"] == "Item 1. Business"


def test_official_event_group_uses_evidence_ids_without_synthetic_event_id():
    tools = SimpleNamespace(
        ledger={"event-p1": {"id": "event-p1", "filing": {"filed": "2026-09-01"}}}
    )
    tools.get_company_updates = lambda ticker: {
        "sections": {"official_events": [{"accession": "event", "evidence_ids": ["event-p1"]}]}
    }
    service = SimpleNamespace(
        capabilities=lambda ticker: {"filings_supported": False},
        corpus=SimpleNamespace(catalog=lambda *args: []),
        enrich_cached_facts=lambda ticker: None,
        tools=lambda as_of: tools,
    )
    page = build_company_page(service, "AAPL", date(2026, 10, 8), section="recent_events")
    assert page["sections"][0]["evidence_ids"] == ["event-p1"]
    assert page["sections"][0]["source_dates"] == ["2026-09-01"]


def test_company_page_cancellation_is_not_downgraded_to_a_source_gap(tmp_path):
    service = service_at(tmp_path)
    control = ResearchControl()
    control.cancel()
    with research_scope(control), pytest.raises(ResearchCancelled):
        build_company_page(service, "AAPL", date(2026, 10, 8), include_market=False)


@pytest.mark.parametrize(
    "params", [{"section": "unknown"}, {"as_of": date(2099, 1, 1).isoformat()}]
)
def test_company_page_rejects_invalid_scope(tmp_path, params):
    with TestClient(create_app(service_at(tmp_path))) as client:
        assert client.get("/api/companies/AAPL/research-page", params=params).status_code == 422


def test_quarter_sequence_refresh_preserves_citations_and_actual_dates(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    filing = Filing(
        id="AAPL:quarter",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-25-000080",
        form="10-Q",
        filed="2025-11-01",
        period_end="2025-09-30",
        source_url="https://www.sec.gov/quarter.htm",
        content_sha256="a" * 64,
    )
    fact = Fact(
        id="quarter-revenue",
        filing_id=filing.id,
        ticker="AAPL",
        metric="revenue",
        label="Revenue",
        concept="us-gaap:Revenue",
        value="123.456",
        unit="USD",
        fiscal_year=2025,
        start="2025-07-01",
        end="2025-09-30",
        source_url=filing.source_url,
        locator="q3",
    )
    service.corpus.ingest(filing, [fact], [])
    calls = []
    monkeypatch.setattr(
        service.discovery,
        "prepare_quarterly",
        lambda *args: calls.append(args) or {"checked": True},
    )
    with TestClient(create_app(service)) as client:
        response = client.get(
            "/api/companies/AAPL/quarterly-sequence?as_of=2026-10-08&refresh=true"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["periods"][0]["period_end"] == "2025-09-30"
        ids = {e["id"] for e in data["evidence"]}
        assert set(data["periods"][0]["evidence_ids"]) <= ids
        assert data["source_preparation"]["checked"] is True
        assert calls
        assert client.get("/api/companies/AAPL/quarterly-sequence?quarters=99").status_code == 422


def dcf_fixture(service):
    filing = Filing(
        id="AAPL:dcf",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-25-000090",
        form="10-K",
        filed="2025-10-31",
        period_end="2025-09-27",
        source_url="https://www.sec.gov/dcf.htm",
        content_sha256="b" * 64,
    )
    common = dict(
        filing_id=filing.id,
        ticker="AAPL",
        unit="USD",
        fiscal_year=2025,
        start="2024-09-29",
        end="2025-09-27",
        source_url=filing.source_url,
        locator="cashflow",
    )
    cash = Fact(
        id="dcf-cash",
        metric="operating_cash_flow",
        label="OCF",
        concept="us-gaap:NetCashProvidedByUsedInOperatingActivities",
        value="100",
        **common,
    )
    capex = Fact(
        id="dcf-capex",
        metric="cash_capex",
        label="PP&E purchases",
        concept="us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        value="30",
        **common,
    )
    shares = StatementFact(
        id="dcf-shares",
        filing_id=filing.id,
        ticker="AAPL",
        statement="balance",
        metric="shares_outstanding",
        label="Common shares",
        concept="dei:EntityCommonStockSharesOutstanding",
        value="10",
        unit="shares",
        fiscal_year=2025,
        end="2025-09-27",
        source_url=filing.source_url,
        locator="cover",
    )
    service.corpus.ingest(filing, [cash, capex], [], statement_facts=[shares])
    return (
        dict(
            ticker="AAPL",
            as_of="2026-10-08",
            currency="USD",
            base_free_cash_flow="70",
            shares_outstanding="10",
            forecast_years=2,
            growth_pct="0",
            discount_pct="10",
            terminal_growth_pct="2",
            source_evidence_ids=[cash.id, capex.id, shares.id],
        ),
        filing,
        cash,
        capex,
        shares,
    )


def test_sourced_dcf_matches_actual_inputs_without_claiming_enterprise_value(tmp_path):
    service = service_at(tmp_path)
    payload, *_ = dcf_fixture(service)
    with TestClient(create_app(service)) as client:
        response = client.post("/api/valuation/dcf", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["schema"] == "filingcopilot.dcf/v2"
        assert data["source_validation"]["status"] == "matched_to_source_inputs"
        assert len(data["evidence"]) == 3
        assert "enterprise_value" not in data["base_case"]
        assert data["source_validation"]["shares_date"] == "2025-09-27"


def test_dcf_input_selection_offers_only_source_matched_inputs(tmp_path):
    service = service_at(tmp_path)
    payload, *_ = dcf_fixture(service)
    with TestClient(create_app(service)) as client:
        response = client.get("/api/companies/AAPL/dcf-inputs?as_of=2026-10-08")
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["inputs"]["base_free_cash_flow"] == "70"
        assert set(data["inputs"]["source_evidence_ids"]) == set(payload["source_evidence_ids"])
        assert (
            client.get("/api/companies/AAPL/dcf-inputs?as_of=2025-10-30").json()["found"] is False
        )


@pytest.mark.parametrize(
    "change",
    [
        {"source_evidence_ids": ["invented"]},
        {"base_free_cash_flow": "71"},
        {"shares_outstanding": "11"},
        {"currency": "CNY"},
        {"ticker": "MSFT"},
        {"as_of": "2025-10-30"},
        {"discount_grid": ["-1", "10"]},
    ],
)
def test_sourced_dcf_rejects_unmatched_or_unavailable_inputs(tmp_path, change):
    service = service_at(tmp_path)
    payload, *_ = dcf_fixture(service)
    with TestClient(create_app(service)) as client:
        assert client.post("/api/valuation/dcf", json={**payload, **change}).status_code == 422


def test_sourced_dcf_rejects_weighted_average_denominator(tmp_path):
    service = service_at(tmp_path)
    payload, filing, cash, capex, shares = dcf_fixture(service)
    weighted = shares.model_copy(
        update={
            "concept": "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "start": cash.start,
        }
    )
    service.corpus.ingest(filing, [cash, capex], [], statement_facts=[weighted])
    with TestClient(create_app(service)) as client:
        assert client.post("/api/valuation/dcf", json=payload).status_code == 422
