from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.domain import Company
from filingcopilot.ingestion import extract_passages
from filingcopilot.models import Fact, Filing, Passage
from filingcopilot.monitoring.evaluation import score_events
from filingcopilot.monitoring.models import (
    Brief,
    Case,
    DigestRequest,
    DisclosureEvent,
    FactDelta,
    PolicyPayload,
    ReplayRequest,
    ReviewRequest,
    ScanRequest,
    WatchPolicy,
)
from filingcopilot.monitoring.taxonomy import (
    classify_8k,
    fact_materiality,
    parse_8k_items,
    section_key,
)
from filingcopilot.monitoring.verify import attach_integrity, verify_case_package
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.updates import _scope, acquire_updates


@pytest.fixture
def service(tmp_path):
    instance = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    instance.watchlist.lookup = lambda ticker, translate=True: {
        "candidates": [{"ticker": ticker, "name": ticker}]
    }
    instance.watchlist.add("TEST")
    return instance


def _filing(accession: str, form: str, filed: date, period_end: date) -> Filing:
    return Filing(
        id=f"TEST:{accession}",
        ticker="TEST",
        company="Test Corp.",
        cik="0000320193",
        accession=accession,
        form=form,
        filed=filed,
        period_end=period_end,
        source_url=f"https://www.sec.gov/Archives/edgar/data/320193/{accession}/doc.html",
        content_sha256="a" * 64,
    )


def _ingest(
    service,
    *,
    accession: str,
    form: str = "10-K",
    filed: date,
    period_end: date,
    revenue: str,
    risk_text: str,
) -> Filing:
    filing = _filing(accession, form, filed, period_end)
    fiscal_year = period_end.year
    fact = Fact(
        id=f"fact-{accession}-revenue",
        filing_id=filing.id,
        ticker="TEST",
        metric="revenue",
        label="营业收入",
        concept="us-gaap:Revenue",
        value=Decimal(revenue),
        unit="USD",
        fiscal_year=fiscal_year,
        start=date(fiscal_year - 1, 9, 29),
        end=period_end,
        source_url=filing.source_url,
        locator=f"facts.us-gaap.Revenue.units.USD[0]; accession={accession}",
    )
    passage = Passage(
        id=f"passage-{accession}-risk",
        filing_id=filing.id,
        ticker="TEST",
        section="Risk Factors",
        text=risk_text,
        source_url=filing.source_url,
        locator=f"item-1a; accession={accession}",
        page=12,
    )
    service.corpus.ingest(filing, [fact], [passage])
    return filing


def _seed_two_annuals(service):
    _ingest(
        service,
        accession="0000320193-24-000001",
        filed=date(2024, 10, 31),
        period_end=date(2024, 9, 28),
        revenue="390000000000",
        risk_text="The company faces competition and supply chain risk.",
    )
    _ingest(
        service,
        accession="0000320193-25-000002",
        filed=date(2025, 10, 31),
        period_end=date(2025, 9, 27),
        revenue="416000000000",
        risk_text=(
            "The company faces competition, supply chain risk and a new "
            "regulatory review of its services business."
        ),
    )


def test_8k_item_classification_and_materiality():
    assert parse_8k_items("2.02, 9.01") == ["2.02", "9.01"]
    specs = classify_8k("2.02,9.01")
    assert len(specs) == 1
    assert specs[0].event_type == "financial_results"
    assert specs[0].subtype == "earnings_release"
    assert classify_8k("9.01") == []

    small = FactDelta(
        metric="revenue",
        current_value="110",
        prior_value="100",
        unit="USD",
        change_pct=10.0,
        change_abs="10",
    )
    label, _score, _hits = fact_materiality([small])
    assert label == "suppressed"

    large = FactDelta(
        metric="revenue",
        current_value="120000000",
        prior_value="100000000",
        unit="USD",
        change_pct=20.0,
        change_abs="20000000",
    )
    label, score, hits = fact_materiality([large])
    assert label == "high"
    assert score >= 70
    assert "revenue_pct_ge_5" in hits
    assert "revenue_abs_ge_10m" in hits


def test_diff_builds_facts_and_targeted_sections(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    assert diff["prior_filing_id"]
    assert diff["prior_filing_id"] != current["id"]
    assert diff["comparison_basis"] == "prior_annual"
    revenue = next(item for item in diff["facts"] if item["metric"] == "revenue")
    assert revenue["current_value"] == "416000000000"
    assert revenue["prior_value"] == "390000000000"
    assert revenue["change_pct"] > 5
    assert diff["sections"]
    assert diff["sections"][0]["similarity"] < 1.0
    # Re-running the same diff must be idempotent (derived rows are upserted).
    again = service.monitoring.diff.build(current)
    assert again["id"] == diff["id"]
    assert again["facts"]


def test_events_and_brief_are_deterministic(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    policy = WatchPolicy(ticker="TEST")
    events = service.monitoring.events.build(current, policy, diff)
    assert any(event.event_type == "financial_results" for event in events)
    brief = service.monitoring.briefs.build(current, events, diff, use_model=False)
    assert brief.ticker == "TEST"
    assert brief.numbers
    assert brief.sources
    assert brief.generator == "template"
    assert all(item.kind == "inference" for item in brief.why_matters)
    assert all(item.basis_evidence_ids for item in brief.why_matters)


def test_scan_creates_case_and_review_transitions(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    result = service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    assert result.cases_created == 1
    cases = service.monitoring.list_cases()
    assert len(cases) == 1
    case = cases[0]
    assert case["status"] == "pending_review"
    assert case["brief"]["sources"]

    approved = service.monitoring.review_case(
        case["id"],
        ReviewRequest(
            action="approve",
            actor="analyst",
            comment="looks right",
            brief_version=case["brief_version"],
        ),
    )
    assert approved["case"]["status"] == "approved"
    assert approved["reviews"][0]["action"] == "approve"

    # A second scan must not re-create cases or re-process the same filing.
    again = service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    assert again.filings_seen == 0
    assert again.cases_created == 0
    assert len(service.monitoring.list_cases()) == 1


def test_export_package_verifies_and_detects_tampering(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    case = service.monitoring.list_cases()[0]
    package = service.monitoring.export_case(case["id"])
    report = verify_case_package(package)
    assert report["status"] == "consistent"
    assert report["checks_failed"] == 0
    assert report["sources"] > 0

    package["case"]["title"] = "tampered"
    assert verify_case_package(package)["status"] == "invalid"

    delivery = service.monitoring.deliver(case["id"], "local")
    assert delivery["status"] == "recorded"


def test_case_api_approve_and_edit(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    with TestClient(create_app(service)) as client:
        cases = client.get("/api/monitoring/cases").json()["cases"]
        assert len(cases) == 1
        events = client.get("/api/monitoring/events").json()["events"]
        assert events
        assert all(event["evidence_ids"] for event in events)
        case_id = cases[0]["id"]
        response = client.post(
            f"/api/monitoring/cases/{case_id}/approve",
            json={
                "action": "approve",
                "actor": "api-test",
                "comment": "ok",
                "brief_version": cases[0]["brief_version"],
            },
        )
        assert response.status_code == 200
        assert response.json()["case"]["status"] == "approved"

        edited = client.post(
            f"/api/monitoring/cases/{case_id}/edit",
            json={
                "action": "edit",
                "actor": "api-test",
                "reason_code": "wording",
                "brief_version": cases[0]["brief_version"],
                "brief": {
                    **cases[0]["brief"],
                    "headline": "Analyst-edited headline",
                },
            },
        )
        assert edited.status_code == 200
        assert edited.json()["case"]["status"] == "edited"
        assert edited.json()["case"]["brief"]["headline"] == "Analyst-edited headline"
        assert edited.json()["reviews"][-1]["edit_diff"]


def test_policy_update_is_versioned(service):
    service.monitoring.policies()
    policy = service.monitoring.set_policy(
        PolicyPayload(
            ticker="TEST",
            watch=["revenue", "debt"],
            materiality_floor="high",
        )
    )
    assert policy["watch"] == ["revenue", "debt"]
    assert policy["materiality_floor"] == "high"
    assert policy["version"] == 2


def test_quarterly_diff_uses_prior_year_same_quarter(service):
    service.monitoring.today = date(2025, 11, 4)
    current = _filing(
        "0000320193-25-000010", "10-Q", date(2025, 8, 1), date(2025, 6, 28)
    )
    current_fact = Fact(
        id="fact-2025-q3-revenue",
        filing_id=current.id,
        ticker="TEST",
        metric="revenue",
        label="营业收入",
        concept="us-gaap:Revenue",
        value=Decimal("1000"),
        unit="USD",
        fiscal_year=2025,
        start=date(2025, 3, 30),
        end=date(2025, 6, 28),
        source_url=current.source_url,
        locator="facts.q3",
    )
    service.corpus.ingest(current, [current_fact], [])
    prior = _filing(
        "0000320193-24-000010", "10-Q", date(2024, 8, 1), date(2024, 6, 29)
    )
    prior_fact = Fact(
        id="fact-2024-q3-revenue",
        filing_id=prior.id,
        ticker="TEST",
        metric="revenue",
        label="营业收入",
        concept="us-gaap:Revenue",
        value=Decimal("800"),
        unit="USD",
        fiscal_year=2024,
        start=date(2024, 3, 31),
        end=date(2024, 6, 29),
        source_url=prior.source_url,
        locator="facts.q3-prior",
    )
    service.corpus.ingest(prior, [prior_fact], [])
    diff = service.monitoring.diff.build(
        next(
            item
            for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
            if item["id"] == current.id
        )
    )
    revenue = next(item for item in diff["facts"] if item["metric"] == "revenue")
    assert revenue["comparison_basis"] == "prior_year_quarter"
    assert revenue["change_pct"] == 25.0


def test_brief_model_budget_makes_one_request(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    events = service.monitoring.events.build(
        current, WatchPolicy(ticker="TEST"), diff
    )
    template = service.monitoring.briefs.build(
        current, events, diff, use_model=False
    )
    source_id = template.sources[0].id
    calls = {"once": 0, "complete": 0}

    class BudgetGateway:
        def complete_once(self, messages, *, timeout, max_tokens):
            calls["once"] += 1
            assert timeout > 0 and max_tokens > 0
            return {
                "message": {
                    "content": __import__("json").dumps(
                        {
                            "headline": "Budgeted headline",
                            "why_matters": [
                                {
                                    "text": "Budgeted inference",
                                    "basis_evidence_ids": [source_id],
                                }
                            ],
                            "risk_summaries": [
                                {
                                    "section": template.risks[0].section,
                                    "text": "Semantic risk summary",
                                    "basis_evidence_ids": [source_id],
                                }
                            ]
                            if template.risks
                            else [],
                        }
                    )
                },
                "usage": {},
            }

        def complete(self, *args, **kwargs):
            calls["complete"] += 1
            raise AssertionError("brief must not use the retrying gateway")

    service.monitoring.gateway = BudgetGateway()
    brief = service.monitoring.briefs.build(
        current, events, diff, use_model=True
    )
    assert calls == {"once": 1, "complete": 0}
    assert brief.generator == "model"
    assert brief.headline == "Budgeted headline"
    if template.risks:
        assert brief.risks[0].summary_kind == "model"
        assert brief.risks[0].semantic_summary == "Semantic risk summary"


def test_section_delta_has_char_offsets_and_quote_hash(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    section = diff["sections"][0]
    assert section["char_start"] is not None
    assert section["char_end"] is not None
    assert len(section["quote_hash"]) == 64


def test_verifier_rejects_quote_hash_and_char_span_tampering(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    case = service.monitoring.list_cases()[0]
    package = service.monitoring.export_case(case["id"])
    passage = next(
        item for item in package["case"]["brief"]["sources"]
        if item.get("kind") == "passage"
    )
    passage["quote"] = "tampered quote"
    tampered = attach_integrity(package)
    report = verify_case_package(tampered)
    assert report["status"] == "invalid"
    assert any("quote" in item for item in report["errors"])

    invalid_span = deepcopy(package)
    source = next(
        item for item in invalid_span["case"]["brief"]["sources"]
        if item.get("kind") == "passage"
    )
    source["char_start"] = -1
    source["char_end"] = 0
    invalid_span = attach_integrity(invalid_span)
    span_report = verify_case_package(invalid_span)
    assert span_report["status"] == "invalid"
    assert any("char span" in item for item in span_report["errors"])

    if package["case"]["brief"].get("risks"):
        invalid_risk = deepcopy(package)
        invalid_risk["case"]["brief"]["risks"][0]["char_start"] = -1
        invalid_risk = attach_integrity(invalid_risk)
        risk_report = verify_case_package(invalid_risk)
        assert risk_report["status"] == "invalid"
        assert any("risk" in item for item in risk_report["errors"])


def test_model_failure_falls_back_to_template(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    events = service.monitoring.events.build(
        current, WatchPolicy(ticker="TEST"), diff
    )

    class BrokenGateway:
        def complete(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    service.monitoring.gateway = BrokenGateway()
    brief = service.monitoring.briefs.build(current, events, diff, use_model=True)
    assert brief.generator == "template"


def test_api_base_accepts_full_chat_completions_url(monkeypatch):
    from filingcopilot.settings import Settings

    monkeypatch.setenv("FC_API_BASE", "https://example.com/v1/chat/completions")
    assert Settings.from_env().api_base == "https://example.com/v1"


def test_watch_policy_filters_event_types(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    policy = WatchPolicy(ticker="TEST", watch=["revenue"])
    events = service.monitoring.events.build(current, policy, diff)
    actionable = service.monitoring.events.actionable(events, policy)
    assert actionable
    assert all(event.event_type == "financial_results" for event in actionable)


def test_8k_events_bind_item_specific_passages(service):
    service.monitoring.today = date(2025, 11, 4)
    filing = _filing(
        "0000320193-25-000099", "8-K", date(2025, 11, 1), date(2025, 11, 1)
    )
    passages = [
        Passage(
            id="passage-8k-202",
            filing_id=filing.id,
            ticker="TEST",
            section="Item 2.02 Results of Operations",
            text="Results of operations: revenue increased 10% in the quarter.",
            source_url=filing.source_url,
            locator="item-2.02",
        ),
        Passage(
            id="passage-8k-502",
            filing_id=filing.id,
            ticker="TEST",
            section="Item 5.02 Departure of Directors",
            text="The chief financial officer resigned effective November 1.",
            source_url=filing.source_url,
            locator="item-5.02",
        ),
        Passage(
            id="passage-8k-cover",
            filing_id=filing.id,
            ticker="TEST",
            section="Cover",
            text="Cover page of the current report.",
            source_url=filing.source_url,
            locator="cover",
        ),
    ]
    service.corpus.ingest(filing, [], passages)
    events = service.monitoring.events.build(
        filing.model_dump(mode="json"),
        WatchPolicy(ticker="TEST"),
        {},
        {"items": "2.02,5.02"},
    )
    by_subtype = {event.subtype: event for event in events}
    assert by_subtype["earnings_release"].evidence_ids == ["passage-8k-202"]
    assert by_subtype["leadership_change"].evidence_ids == ["passage-8k-502"]


def test_guidance_rejects_explicit_refusal(service):
    service.monitoring.today = date(2025, 11, 4)
    negative = _filing(
        "0000320193-25-000098", "8-K", date(2025, 11, 1), date(2025, 11, 1)
    )
    service.corpus.ingest(
        negative,
        [],
        [
            Passage(
                id="passage-8k-no-guidance",
                filing_id=negative.id,
                ticker="TEST",
                section="Item 2.02",
                text="The company does not provide guidance for future periods.",
                source_url=negative.source_url,
                locator="item-2.02",
            )
        ],
    )
    events = service.monitoring.events.build(
        negative.model_dump(mode="json"),
        WatchPolicy(ticker="TEST"),
        {},
        {"items": "2.02"},
    )
    assert not any(event.event_type == "guidance" for event in events)

    positive = _filing(
        "0000320193-25-000097", "8-K", date(2025, 11, 2), date(2025, 11, 2)
    )
    release = positive.model_copy(
        update={"id": positive.id + ":EX-99.1", "form": "8-K EX-99.1"}
    )
    service.corpus.ingest(
        positive,
        [],
        [
            Passage(
                id="passage-8k-cover-positive",
                filing_id=positive.id,
                ticker="TEST",
                section="Cover",
                text="Cover page of the current report.",
                source_url=positive.source_url,
                locator="cover",
            )
        ],
    )
    service.corpus.ingest(
        release,
        [],
        [
            Passage(
                id="passage-8k-guidance",
                filing_id=release.id,
                ticker="TEST",
                section="Exhibit 99.1",
                text="We expect revenue growth to accelerate to 10% in 2027.",
                source_url=release.source_url,
                locator="ex-99.1",
            )
        ],
    )
    events = service.monitoring.events.build(
        positive.model_dump(mode="json"),
        WatchPolicy(ticker="TEST"),
        {},
        {"items": "2.02"},
    )
    guidance = next(event for event in events if event.event_type == "guidance")
    assert guidance.evidence_ids == ["passage-8k-guidance"]


def test_guidance_disclaimer_is_not_guidance(service):
    service.monitoring.today = date(2025, 11, 4)
    filing = _filing(
        "0000320193-25-000095", "8-K", date(2025, 11, 1), date(2025, 11, 1)
    )
    release = filing.model_copy(
        update={"id": filing.id + ":EX-99.1", "form": "8-K EX-99.1"}
    )
    service.corpus.ingest(
        filing,
        [],
        [
            Passage(
                id="passage-disclaimer-cover",
                filing_id=filing.id,
                ticker="TEST",
                section="Cover",
                text="Cover page of the current report.",
                source_url=filing.source_url,
                locator="cover",
            )
        ],
    )
    service.corpus.ingest(
        release,
        [],
        [
            Passage(
                id="passage-disclaimer",
                filing_id=release.id,
                ticker="TEST",
                section="Exhibit 99.1",
                text=(
                    "These forward-looking statements involve risk. We expect "
                    "$10 billion of revenue, but actual results may differ."
                ),
                source_url=release.source_url,
                locator="ex-99.1",
            )
        ],
    )
    events = service.monitoring.events.build(
        filing.model_dump(mode="json"),
        WatchPolicy(ticker="TEST"),
        {},
        {"items": "2.02"},
    )
    assert not any(event.event_type == "guidance" for event in events)


def test_nested_risk_heading_preserves_parent_item(service):
    filing = _filing(
        "0000320193-25-000094", "10-K", date(2025, 10, 31), date(2025, 9, 27)
    )
    html = (
        "<html><body><h2>Item 1A. Risk Factors</h2><h3>Competition</h3>"
        "<p>The company faces intense competition and pricing pressure in every "
        "major market, which could reduce revenue and operating margins.</p>"
        "</body></html>"
    ).encode()
    passages = extract_passages(html, filing)
    matching = [p for p in passages if "intense competition" in p.text]
    assert matching
    assert matching[0].parent_section == "Item 1A. Risk Factors"
    assert section_key(matching[0].parent_section or "") == "risk_factors"


def test_unchanged_cash_does_not_create_liquidity_alert(service):
    service.monitoring.today = date(2025, 11, 4)
    for accession, filed, period_end in (
        ("0000320193-24-000050", date(2024, 10, 31), date(2024, 9, 28)),
        ("0000320193-25-000050", date(2025, 10, 31), date(2025, 9, 27)),
    ):
        filing = _filing(accession, "10-K", filed, period_end)
        service.corpus.ingest(
            filing,
            [
                Fact(
                    id=f"fact-{accession}-cash",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="cash",
                    label="现金",
                    concept="us-gaap:Cash",
                    value=Decimal("10000000"),
                    unit="USD",
                    fiscal_year=period_end.year,
                    start=None,
                    end=period_end,
                    source_url=filing.source_url,
                    locator="balance.cash",
                )
            ],
            [],
        )
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000050"
    )
    diff = service.monitoring.diff.build(current)
    cash = next(item for item in diff["facts"] if item["metric"] == "cash")
    assert cash["change_abs"] == "0"
    events = service.monitoring.events.build(
        current, WatchPolicy(ticker="TEST"), diff
    )
    actionable = service.monitoring.events.actionable(
        events, WatchPolicy(ticker="TEST")
    )
    assert not any(event.event_type == "liquidity_debt" for event in actionable)


def test_digest_uses_continuous_window_and_respects_policy(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    case = service.monitoring.list_cases()[0]
    original = Case.model_validate(case)
    old = original.model_copy(
        update={
            "updated_at": (date.today() - timedelta(days=1)).isoformat()
            + "T15:00:00+00:00",
            "revision": original.revision + 1,
        }
    )
    service.monitoring.store.update_case_if_revision(old, original.revision)
    digest = service.monitoring.digest(DigestRequest())
    assert case["id"] in digest["case_ids"]

    service.monitoring.set_policy(
        PolicyPayload(ticker="TEST", notify=False)
    )
    blocked = service.monitoring.digest(DigestRequest())
    assert blocked["cases"] == 0

    first, first_state = service.monitoring.store.claim_digest(
        "2099-01-01", "local", {"markdown": "x"}
    )
    second, second_state = service.monitoring.store.claim_digest(
        "2099-01-01", "local", {"markdown": "x"}
    )
    assert first and first_state == "claimed"
    assert second is None and second_state == "in_progress"
    service.monitoring.store.finish_digest(first, "failed", {"markdown": "x"})
    retry, retry_state = service.monitoring.store.claim_digest(
        "2099-01-01", "local", {"markdown": "x"}
    )
    assert retry and retry_state == "claimed"


def test_historical_window_is_not_fixed_to_ninety_days():
    assert _scope(date(2026, 11, 11), 300, 20) == (
        "2026-01-16",
        "2026-11-11",
    )
    start = date(2024, 1, 1)
    with pytest.raises(ValueError):
        ReplayRequest(start=start, end=start + timedelta(days=730))
    assert ReplayRequest(start=start, end=start + timedelta(days=729))


def test_8k_items_survive_without_replay_metadata(service):
    service.monitoring.today = date(2025, 11, 4)
    filing = _filing(
        "0000320193-25-000093", "8-K", date(2025, 11, 1), date(2025, 11, 1)
    )
    filing = filing.model_copy(update={"items": "2.02"})
    service.corpus.ingest(
        filing,
        [],
        [
            Passage(
                id="passage-items-202",
                filing_id=filing.id,
                ticker="TEST",
                section="Item 2.02",
                text="Results of operations and financial condition.",
                source_url=filing.source_url,
                locator="item-2.02",
            )
        ],
    )
    events = service.monitoring.events.build(
        filing.model_dump(mode="json"), WatchPolicy(ticker="TEST"), {}, {}
    )
    assert any(event.subtype == "earnings_release" for event in events)


def test_brief_cost_cap_requires_pricing(tmp_path):
    from filingcopilot.settings import Settings

    with pytest.raises(ValueError, match="brief cost cap"):
        Settings(
            home=tmp_path,
            monitoring_brief_max_cost_usd=1.0,
        )


def test_replay_does_not_resurrect_superseded_original_events(service, monkeypatch):
    _seed_two_annuals(service)
    _ingest(
        service,
        accession="0000320193-25-000004",
        form="10-K/A",
        filed=date(2025, 12, 1),
        period_end=date(2025, 9, 27),
        revenue="420000000000",
        risk_text="The company faces competition and a revised regulatory review.",
    )
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_quarterly",
        lambda *args, **kwargs: {"checked": True, "warnings": []},
    )
    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_annual_facts",
        lambda *args, **kwargs: {"checked": True, "warning": None},
    )
    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_updates",
        lambda *args, **kwargs: {"checked": True, "warnings": []},
    )
    service.monitoring.replay(
        ReplayRequest(
            tickers=["TEST"],
            start=date(2025, 1, 1),
            end=date(2025, 12, 15),
            generate_brief=False,
            use_model=False,
        )
    )
    original_events = service.monitoring.store.events_for_filing(
        "TEST:0000320193-25-000002"
    )
    amendment_events = service.monitoring.store.events_for_filing(
        "TEST:0000320193-25-000004"
    )
    assert original_events and amendment_events
    assert all(event.status == "superseded" for event in original_events)
    amendment_ids = {event.id for event in amendment_events}
    assert all(
        event.superseded_by_event_id in amendment_ids
        for event in original_events
    )


def test_same_day_amendment_link_and_ambiguous_original(service):
    service.monitoring.today = date(2025, 12, 15)
    original = _ingest(
        service,
        accession="0000320193-25-000005",
        form="10-K",
        filed=date(2025, 10, 31),
        period_end=date(2025, 9, 27),
        revenue="400000000000",
        risk_text="Original risk text.",
    )
    amendment = _ingest(
        service,
        accession="0000320193-25-000006",
        form="10-K/A",
        filed=date(2025, 10, 31),
        period_end=date(2025, 9, 27),
        revenue="410000000000",
        risk_text="Amended risk text.",
    )
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 12, 15), "TEST")
        if item["id"] == amendment.id
    )
    linked = service.monitoring.diff.build(current)
    assert linked["amends_filing_id"] == original.id

    second_original = _ingest(
        service,
        accession="0000320193-25-000007",
        form="10-K",
        filed=date(2025, 10, 30),
        period_end=date(2025, 9, 27),
        revenue="390000000000",
        risk_text="Second original risk text.",
    )
    ambiguous = service.monitoring.diff.build(current)
    assert ambiguous["amends_filing_id"] is None
    assert any("多个同期间原件" in item for item in ambiguous["gaps"])
    assert second_original.id != original.id


def test_historical_replay_processes_interval(service, monkeypatch):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_quarterly",
        lambda *args, **kwargs: {"checked": True, "warnings": []},
    )
    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_annual_facts",
        lambda *args, **kwargs: {"checked": True, "warning": None},
    )
    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_updates",
        lambda *args, **kwargs: {"checked": True, "warnings": []},
    )
    result = service.monitoring.replay(
        ReplayRequest(
            tickers=["TEST"],
            start=date(2024, 1, 1),
            end=date(2025, 11, 4),
            generate_brief=True,
            use_model=False,
        )
    )
    assert result.filings_seen == 2
    assert result.cases_created == 2
    assert len(result.case_ids) == 2
    assert service.monitoring.list_cases()


def test_amendment_links_and_supersedes_original_event(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 12, 15)
    original = next(
        item
        for item in service.corpus.catalog(date(2025, 12, 15), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    amendment = _ingest(
        service,
        accession="0000320193-25-000003",
        form="10-K/A",
        filed=date(2025, 12, 1),
        period_end=date(2025, 9, 27),
        revenue="420000000000",
        risk_text="The company faces competition and a revised regulatory review.",
    )
    original_diff = service.monitoring.diff.build(original)
    original_events = service.monitoring.events.build(
        original, WatchPolicy(ticker="TEST"), original_diff
    )
    for event in original_events:
        service.monitoring.store.upsert_event(event)
    amendment_diff = service.monitoring.diff.build(
        next(
            item
            for item in service.corpus.catalog(date(2025, 12, 15), "TEST")
            if item["id"] == amendment.id
        )
    )
    assert amendment_diff["amends_filing_id"] == original["id"]
    amendment_events = service.monitoring.events.build(
        next(
            item
            for item in service.corpus.catalog(date(2025, 12, 15), "TEST")
            if item["id"] == amendment.id
        ),
        WatchPolicy(ticker="TEST"),
        amendment_diff,
    )
    stored_amendment = [
        service.monitoring.store.upsert_event(event) for event in amendment_events
    ]
    superseded = service.monitoring.store.supersede_events(
        original["id"], [event.id for event in stored_amendment]
    )
    assert superseded
    current_original = service.monitoring.store.events_for_filing(original["id"])
    assert all(event.status == "superseded" for event in current_original)
    assert all(
        event.superseded_by_event_id == stored_amendment[0].id
        for event in current_original
    )


def test_digest_local_delivery_and_pdf(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    digest = service.monitoring.digest(DigestRequest())
    assert "# FilingCopilot 监控早报" in digest["markdown"]
    assert "TEST" in digest["markdown"]
    delivery = service.monitoring.deliver_digest(DigestRequest(channel="local"))
    assert delivery["status"] == "sent"
    again = service.monitoring.deliver_digest(DigestRequest(channel="local"))
    assert again["status"] == "already_sent"
    pdf = service.monitoring.digest_pdf(DigestRequest())
    assert pdf.startswith(b"%PDF")


def test_exact_accession_backfill_is_not_limited_to_five():
    cik = "0000320193"
    metadata_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    accessions = [f"{cik}-25-0000{index:02d}" for index in range(6)]
    rows = {
        key: [
            "8-K" if key == "form" else None
            for _ in accessions
        ]
        for key in (
            "form",
            "filingDate",
            "accessionNumber",
            "reportDate",
            "primaryDocument",
        )
    }
    rows["form"] = ["8-K"] * 6
    rows["filingDate"] = ["2026-11-05"] * 6
    rows["accessionNumber"] = accessions
    rows["reportDate"] = ["2026-11-05"] * 6
    rows["primaryDocument"] = [f"doc{index}.html" for index in range(6)]
    metadata = {
        "cik": cik,
        "tickers": ["TEST"],
        "filings": {"recent": rows, "files": []},
    }
    html = (
        b"<html><body><p>Results of operations and financial condition "
        b"for the current report, with enough text to be retained as a "
        b"narrative passage for verification.</p></body></html>"
    )

    class Fetcher:
        def fetch(self, url, **kwargs):
            if url == metadata_url:
                return __import__("json").dumps(metadata).encode()
            return html

    domain = SimpleNamespace(
        companies={"TEST": Company(cik=cik, name="Test Corp.")}
    )
    oldest = accessions[-1]
    result = acquire_updates(
        Fetcher(),
        "TEST",
        domain,
        date(2026, 11, 10),
        limit=5,
        accessions=[oldest],
    )
    assert [entry["filing"].accession for entry in result["entries"]] == [oldest]


def test_discovery_registers_accession_and_propagates_failed_prepare(service, monkeypatch):
    service.monitoring.today = date(2025, 11, 4)
    first = {
        "filing_id": "TEST:0000320193-25-000080",
        "accession": "0000320193-25-000080",
        "form": "10-K",
        "accepted": "2025-11-03T12:00:00.000Z",
        "filed": "2025-11-03",
    }
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [first],
        "new": [first],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=False,
            use_model=False,
        )
    )
    row = service.monitoring.store.get_filing("TEST", first["filing_id"])
    assert row is not None and row["status"] == "discovered"

    second = {
        **first,
        "filing_id": "TEST:0000320193-25-000081",
        "accession": "0000320193-25-000081",
        "accepted": "2025-11-04T12:00:00.000Z",
        "filed": "2025-11-04",
    }
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [first, second],
        "new": [first, second],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=False,
            use_model=False,
        )
    )
    row = service.monitoring.store.get_filing("TEST", second["filing_id"])
    assert row is not None and row["status"] == "pending"

    monkeypatch.setattr(
        service.monitoring.company_discovery,
        "prepare_quarterly",
        lambda ticker, as_of: {"checked": False, "warning": "refresh timeout"},
    )
    prepared = service.monitoring.discovery.prepare(
        "TEST", date(2025, 11, 4), ["10-Q"]
    )
    assert any("检查未完成" in item for item in prepared["warnings"])


def test_processing_failure_is_retried_on_next_scan(service, monkeypatch):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    real_build = service.monitoring.diff.build
    calls = {"count": 0}

    def flaky_build(filing):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated extraction failure")
        return real_build(filing)

    monkeypatch.setattr(service.monitoring.diff, "build", flaky_build)
    first = service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    assert first.cases_created == 0
    assert any("处理未完成" in item for item in first.warnings)

    second = service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    assert second.filings_seen == 1
    assert second.cases_created == 1


def test_financial_diff_uses_current_accession_and_instant_cash(service):
    service.monitoring.today = date(2025, 11, 4)
    current = _filing(
        "0000320193-25-000060", "10-Q", date(2025, 8, 1), date(2025, 6, 28)
    )
    amended = _filing(
        "0000320193-25-000061", "10-Q/A", date(2025, 9, 1), date(2025, 6, 28)
    )
    prior = _filing(
        "0000320193-24-000060", "10-Q", date(2024, 8, 1), date(2024, 6, 29)
    )
    for filing, revenue, gross_profit, cash in (
        (current, "1000", "600", "500"),
        (amended, "2000", "1400", "900"),
        (prior, "800", "400", "300"),
    ):
        start = date(filing.period_end.year, 3, 30)
        service.corpus.ingest(
            filing,
            [
                Fact(
                    id=f"fact-{filing.accession}-revenue",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="revenue",
                    label="营业收入",
                    concept="us-gaap:Revenue",
                    value=Decimal(revenue),
                    unit="USD",
                    fiscal_year=filing.period_end.year,
                    start=start,
                    end=filing.period_end,
                    source_url=filing.source_url,
                    locator="income.revenue",
                ),
                Fact(
                    id=f"fact-{filing.accession}-gross",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="gross_profit",
                    label="毛利润",
                    concept="us-gaap:GrossProfit",
                    value=Decimal(gross_profit),
                    unit="USD",
                    fiscal_year=filing.period_end.year,
                    start=start,
                    end=filing.period_end,
                    source_url=filing.source_url,
                    locator="income.gross",
                ),
                Fact(
                    id=f"fact-{filing.accession}-cash",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="cash",
                    label="现金",
                    concept="us-gaap:Cash",
                    value=Decimal(cash),
                    unit="USD",
                    fiscal_year=filing.period_end.year,
                    start=None,
                    end=filing.period_end,
                    source_url=filing.source_url,
                    locator="balance.cash",
                ),
            ],
            [],
        )
    diff = service.monitoring.diff.build(
        next(
            item
            for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
            if item["id"] == current.id
        )
    )
    revenue = next(item for item in diff["facts"] if item["metric"] == "revenue")
    assert revenue["current_value"] == "1000"
    assert revenue["prior_value"] == "800"
    assert revenue["change_pct"] == 25.0
    cash = next(item for item in diff["facts"] if item["metric"] == "cash")
    assert cash["comparison_basis"] == "prior_year_instant"
    margin = next(item for item in diff["facts"] if item["metric"] == "gross_margin")
    assert margin["change_pp"] == 10.0


def test_stale_review_cannot_overwrite_regenerated_version(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    old = service.monitoring.list_cases()[0]
    filing = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == old["filing_id"]
    )
    events = [DisclosureEvent.model_validate(item) for item in old["events"]]
    changed = Brief.model_validate(old["brief"]).model_copy(
        update={"headline": "v2 headline"}
    )
    regenerated = service.monitoring.cases.ensure(
        filing, events, changed, refresh=True
    )
    assert regenerated.brief_version == 2

    # Simulate a review that read v1 and reaches the database after v2 exists.
    stale = Case.model_validate(old).model_copy(
        update={"status": "approved", "approved_version": 1}
    )
    with pytest.raises(ValueError, match="其他操作"):
        service.monitoring.store.update_case_if_revision(stale, 1)
    current = service.monitoring.cases.get(old["id"])
    assert current.brief_version == 2
    assert current.status == "pending_review"


def test_approval_is_not_lost_by_stale_regeneration(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    old = service.monitoring.list_cases()[0]
    approved = service.monitoring.review_case(
        old["id"],
        ReviewRequest(
            action="approve",
            actor="analyst",
            brief_version=old["brief_version"],
        ),
    )
    assert approved["case"]["status"] == "approved"

    # A regeneration that read pending v1 before the approval must not save
    # its stale object after approval. The revision check rejects it.
    stale = Case.model_validate(old).model_copy(
        update={
            "revision": 2,
            "status": "pending_review",
            "brief": Brief.model_validate(old["brief"]).model_copy(
                update={"headline": "stale regeneration"}
            ),
        }
    )
    with pytest.raises(ValueError, match="其他操作"):
        service.monitoring.store.update_case_if_revision(stale, 1)
    current = service.monitoring.cases.get(old["id"])
    assert current.status == "approved"
    assert current.approved_brief is not None
    assert current.revision >= 2


def test_margin_change_creates_financial_event(service):
    service.monitoring.today = date(2025, 11, 4)
    for accession, filed, period_end, gross in (
        ("0000320193-24-000070", date(2024, 8, 1), date(2024, 6, 29), "30"),
        ("0000320193-25-000070", date(2025, 8, 1), date(2025, 6, 28), "40"),
    ):
        filing = _filing(accession, "10-Q", filed, period_end)
        start = date(period_end.year, 3, 30)
        service.corpus.ingest(
            filing,
            [
                Fact(
                    id=f"fact-{accession}-revenue",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="revenue",
                    label="营业收入",
                    concept="us-gaap:Revenue",
                    value=Decimal("100"),
                    unit="USD",
                    fiscal_year=period_end.year,
                    start=start,
                    end=period_end,
                    source_url=filing.source_url,
                    locator="revenue",
                ),
                Fact(
                    id=f"fact-{accession}-gross",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="gross_profit",
                    label="毛利润",
                    concept="us-gaap:GrossProfit",
                    value=Decimal(gross),
                    unit="USD",
                    fiscal_year=period_end.year,
                    start=start,
                    end=period_end,
                    source_url=filing.source_url,
                    locator="gross",
                ),
            ],
            [],
        )
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000070"
    )
    diff = service.monitoring.diff.build(current)
    margin = next(item for item in diff["facts"] if item["metric"] == "gross_margin")
    assert margin["change_pp"] == 10.0
    events = service.monitoring.events.build(
        current, WatchPolicy(ticker="TEST"), diff
    )
    assert any(
        event.event_type == "financial_results" for event in events
    )


def test_margin_does_not_mix_quarter_and_ytd(service):
    service.monitoring.today = date(2025, 11, 4)
    for accession, filed, period_end, net_income in (
        ("0000320193-24-000071", date(2024, 8, 1), date(2024, 6, 29), "20"),
        ("0000320193-25-000071", date(2025, 8, 1), date(2025, 6, 28), "30"),
    ):
        filing = _filing(accession, "10-Q", filed, period_end)
        service.corpus.ingest(
            filing,
            [
                Fact(
                    id=f"fact-{accession}-revenue",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="revenue",
                    label="营业收入",
                    concept="us-gaap:Revenue",
                    value=Decimal("100"),
                    unit="USD",
                    fiscal_year=period_end.year,
                    start=date(period_end.year, 3, 30),
                    end=period_end,
                    source_url=filing.source_url,
                    locator="revenue",
                ),
                Fact(
                    id=f"fact-{accession}-net-income",
                    filing_id=filing.id,
                    ticker="TEST",
                    metric="net_income",
                    label="净利润",
                    concept="us-gaap:NetIncome",
                    value=Decimal(net_income),
                    unit="USD",
                    fiscal_year=period_end.year,
                    start=date(period_end.year, 1, 1),
                    end=period_end,
                    source_url=filing.source_url,
                    locator="net-income",
                ),
            ],
            [],
        )
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000071"
    )
    diff = service.monitoring.diff.build(current)
    assert not any(item["metric"] == "net_margin" for item in diff["facts"])
    assert any("期间/口径" in item for item in diff["gaps"])


def test_unmatched_8k_item_has_no_fake_cover_citation(service):
    service.monitoring.today = date(2025, 11, 4)
    filing = _filing(
        "0000320193-25-000096", "8-K", date(2025, 11, 1), date(2025, 11, 1)
    )
    service.corpus.ingest(
        filing,
        [],
        [
            Passage(
                id="passage-only-cover",
                filing_id=filing.id,
                ticker="TEST",
                section="Cover",
                text="Cover page of the current report.",
                source_url=filing.source_url,
                locator="cover",
            )
        ],
    )
    events = service.monitoring.events.build(
        filing.model_dump(mode="json"),
        WatchPolicy(ticker="TEST"),
        {},
        {"items": "2.03"},
    )
    debt = next(event for event in events if event.subtype == "direct_obligation")
    assert debt.evidence_ids == []
    assert any("证据缺失" in gap for gap in debt.gaps)


def test_regeneration_requires_fresh_approval(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    case = service.monitoring.list_cases()[0]
    approved = service.monitoring.review_case(
        case["id"],
        ReviewRequest(
            action="approve",
            actor="analyst",
            brief_version=case["brief_version"],
        ),
    )
    assert approved["case"]["status"] == "approved"
    assert approved["case"]["approved_version"] == case["brief_version"]

    filing = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == case["filing_id"]
    )
    events = [DisclosureEvent.model_validate(item) for item in case["events"]]
    new_brief = Brief.model_validate(case["brief"]).model_copy(
        update={"headline": "Regenerated headline"}
    )
    regenerated = service.monitoring.cases.ensure(
        filing, events, new_brief, refresh=True
    )
    assert regenerated.brief_version == case["brief_version"] + 1
    assert regenerated.status == "pending_review"
    assert regenerated.approved_version == case["brief_version"]
    assert regenerated.approved_brief is not None
    assert any(
        item["action"] == "regenerate"
        for item in service.monitoring.get_case(case["id"])["reviews"]
    )
    with pytest.raises(ValueError, match="版本"):
        service.monitoring.review_case(
            case["id"],
            ReviewRequest(
                action="approve",
                actor="analyst",
                brief_version=case["brief_version"],
            ),
        )


def test_risk_factor_change_is_emitted(service):
    _seed_two_annuals(service)
    service.monitoring.today = date(2025, 11, 4)
    current = next(
        item
        for item in service.corpus.catalog(date(2025, 11, 4), "TEST")
        if item["id"] == "TEST:0000320193-25-000002"
    )
    diff = service.monitoring.diff.build(current)
    events = service.monitoring.events.build(
        current, WatchPolicy(ticker="TEST"), diff
    )
    risk = next(event for event in events if event.subtype == "risk_factor_change")
    assert risk.materiality in {"high", "medium"}
    assert risk.section_deltas


def test_event_scorer_reports_recall_and_no_event_false_positives():
    gold = {
        "events": [
            {
                "ticker": "AAPL",
                "filing_id": "f1",
                "event_type": "financial_results",
                "subtype": "periodic_results",
            },
            {
                "ticker": "AAPL",
                "filing_id": "f2",
                "event_type": "liquidity_debt",
                "subtype": "periodic_liquidity",
            },
        ],
        "no_event": [{"ticker": "AAPL", "filing_id": "f3"}],
    }
    predicted = {
        "events": [
            {
                "ticker": "AAPL",
                "filing_id": "f1",
                "event_type": "financial_results",
                "subtype": "periodic_results",
            },
            {
                "ticker": "AAPL",
                "filing_id": "f3",
                "event_type": "financial_results",
                "subtype": "periodic_results",
            },
        ]
    }
    report = score_events(gold, predicted)
    assert report["overall"]["tp"] == 1
    assert report["overall"]["fn"] == 1
    assert report["overall"]["fp"] == 1
    assert report["no_event_false_positives"] == [
        "AAPL|f3|financial_results|periodic_results"
    ]
    assert report["no_event_fp_rate"] == 1.0
    assert report["overall"]["recall_ci"][0] <= report["overall"]["recall"]
    assert report["per_type"]["financial_results"]["precision_ci"]


def test_verifier_recomputes_numbers_and_quotes(service):
    _seed_two_annuals(service)
    service.monitoring.discovery.refresh = lambda ticker, as_of, forms: {
        "ticker": ticker,
        "metadata": [],
        "new": [],
        "prepared": [],
        "warnings": [],
    }
    service.monitoring.scan(
        ScanRequest(
            tickers=["TEST"],
            as_of=date(2025, 11, 4),
            bootstrap=True,
            generate_brief=True,
            use_model=False,
        )
    )
    case = service.monitoring.list_cases()[0]
    package = service.monitoring.export_case(case["id"])
    assert verify_case_package(package)["status"] == "consistent"

    # A fact may be reused by two events (financial results and liquidity).
    reused = deepcopy(package)
    events = reused["case"]["events"]
    events.append(
        {
            **deepcopy(events[0]),
            "event_type": "liquidity_debt",
            "subtype": "periodic_liquidity",
            "label": "Liquidity",
        }
    )
    reused = attach_integrity(reused)
    assert verify_case_package(reused)["status"] == "consistent"

    # A duplicate inside one event is still an error.
    duplicate_event = deepcopy(package)
    event = duplicate_event["case"]["events"][0]
    if event.get("facts"):
        event["facts"].append(deepcopy(event["facts"][0]))
        duplicate_event = attach_integrity(duplicate_event)
        report = verify_case_package(duplicate_event)
        assert report["status"] == "invalid"
        assert any("duplicate" in item for item in report["errors"])

    tampered = attach_integrity(
        {
            **package,
            "case": {
                **package["case"],
                "brief": {
                    **package["case"]["brief"],
                    "numbers": [
                        {
                            **package["case"]["brief"]["numbers"][0],
                            "current_value": "999999999",
                            "change_pct": 999.0,
                        },
                        *package["case"]["brief"]["numbers"][1:],
                    ],
                },
            },
        }
    )
    report = verify_case_package(tampered)
    assert report["status"] == "invalid"
    assert report["content_status"] == "failed"
    assert any("does not match evidence" in item or "change_pct" in item for item in report["errors"])

    unit_tampered = attach_integrity(
        {
            **package,
            "case": {
                **package["case"],
                "brief": {
                    **package["case"]["brief"],
                    "numbers": [
                        {**package["case"]["brief"]["numbers"][0], "unit": "INVALID_UNIT"},
                        *package["case"]["brief"]["numbers"][1:],
                    ],
                },
            },
        }
    )
    unit_report = verify_case_package(unit_tampered)
    assert unit_report["status"] == "invalid"
    assert any("unit" in item for item in unit_report["errors"])

    duplicate = attach_integrity(
        {
            **package,
            "case": {
                **package["case"],
                "brief": {
                    **package["case"]["brief"],
                    "numbers": [
                        package["case"]["brief"]["numbers"][0],
                        {
                            **package["case"]["brief"]["numbers"][0],
                            "current_value": "999999999",
                            "change_pct": 999999.0,
                        },
                    ],
                },
            },
        }
    )
    duplicate_report = verify_case_package(duplicate)
    assert duplicate_report["status"] == "invalid"
    assert any("duplicate" in item for item in duplicate_report["errors"])






