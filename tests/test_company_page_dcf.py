from datetime import date
from decimal import Decimal

import pytest

from filingcopilot.api import create_app
from filingcopilot.company_page import PAGE_SECTIONS, build_company_page
from filingcopilot.dcf import DcfRequest, dcf_sensitivity
from filingcopilot.market import MarketError
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_company_page_has_independent_blocks_and_keeps_market_gap(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market.history = lambda *a, **k: (_ for _ in ()).throw(MarketError("offline fixture"))
    page = build_company_page(service, "AAPL", date(2026, 10, 8))
    assert page["schema"] == "filingcopilot.company-page/v1"
    assert {section["key"] for section in page["sections"]} == set(PAGE_SECTIONS)
    valuation = next(section for section in page["sections"] if section["key"] == "valuation")
    assert valuation["status"] in {"partial", "gap"}
    assert valuation["gap"]
    assert page["evidence"]


def test_dcf_facts_assumptions_and_sensitivity_replay_deterministically():
    request = DcfRequest(
        ticker="AAPL",
        as_of=date(2026, 10, 8),
        currency="USD",
        base_free_cash_flow=Decimal("100"),
        shares_outstanding=Decimal("10"),
        forecast_years=2,
        growth_pct=Decimal("0"),
        discount_pct=Decimal("10"),
        terminal_growth_pct=Decimal("2"),
        growth_grid=[Decimal("0"), Decimal("5")],
        discount_grid=[Decimal("10"), Decimal("12")],
        source_evidence_ids=["fact-fcf", "fact-shares"],
    )
    result = dcf_sensitivity(request)
    assert result["inputs"]["source_evidence_ids"] == ["fact-fcf", "fact-shares"]
    assert result["source_validation"]["status"] == "not_checked"
    assert "enterprise_value" not in result["base_case"]
    assert result["user_assumptions"]["growth_pct"] == "0"
    assert len(result["sensitivity"]) == 4
    assert Decimal(result["base_case"]["implied_value_per_share"]) > 0
    assert "不是公司指引" in result["note"]


def test_page_and_dcf_are_exposed_as_explicit_api_surfaces(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    app = create_app(service)
    routes = {route.path for route in app.routes}
    assert "/api/companies/{ticker}/research-page" in routes
    assert "/api/valuation/dcf" in routes
