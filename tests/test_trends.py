import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package, digest
from filingcopilot.models import Fact, Filing, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.trends import TrendRequest, is_financial_trend_question, trend_years

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

DAY = date(2026, 10, 8)


@pytest.fixture
def service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.discovery.prepare_financial_trend = lambda *args: {"checked": True}
    return service


def request(**kwargs):
    return TrendRequest(language="zh", ticker="AAPL", as_of=DAY, **kwargs)


def data(answer):
    return answer.trace[0]["result"]


def add(
    service,
    name,
    *,
    filed="2026-02-01",
    period="2025-09-27",
    year=2025,
    values=None,
    start="2024-09-29",
    unit="USD",
):
    filing = Filing(
        id=name,
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-26-000001",
        form="10-K",
        filed=filed,
        period_end=period,
        source_url="https://www.sec.gov/Archives/" + name + ".htm",
        content_sha256="a" * 64,
    )
    facts = [
        Fact(
            id=name + "-" + str(i),
            filing_id=name,
            ticker="AAPL",
            metric=metric,
            label=metric,
            concept="us-gaap:Test",
            value=value,
            unit=unit,
            fiscal_year=year,
            start=start,
            end=period,
            source_url=filing.source_url,
            locator="bound annual test row",
        )
        for i, (metric, value) in enumerate(values or [])
    ]
    service.corpus.ingest(filing, facts, [])
    return filing


@pytest.mark.parametrize("years", [0, 2, 4, 10])
def test_request_rejects_unsupported_windows(years):
    with pytest.raises(ValueError):
        request(years=years)


def test_request_normalizes_scope_and_disallows_future():
    assert TrendRequest(language="zh", ticker=" aapl ").ticker == "AAPL"
    with pytest.raises(ValueError):
        TrendRequest(language="zh", ticker="AAPL", as_of=date(2099, 1, 1))


@pytest.mark.parametrize(
    "question,expected",
    [
        ("过去五年营收、利润和现金流趋势怎么样？", True),
        ("近三年营业收入", True),
        ("3-year revenue trend", True),
        ("历年净利润", True),
        ("过去五年股票涨跌", False),
        ("近五年现金流为什么下降", False),
        ("TTM 营收趋势", False),
        ("近三年季度收入", False),
        ("近三年营业利润率和营收趋势", False),
        ("近五年营收同比增长率", False),
        ("3-year revenue growth and margin", False),
        ("近五年现金流和净资本开支", False),
        ("FY2024 之前三年的营收", False),
        ("近三年毛利润趋势", False),
        ("近五年营收和总资产", False),
    ],
)
def test_narrow_numeric_intent(question, expected):
    assert is_financial_trend_question(question) is expected
    assert trend_years("近三年营收") == 3


def test_five_year_window_retains_missing_years_and_zero_model_evidence(service):
    answer = service.financial_trend(request())
    assert [r["fiscal_year"] for r in data(answer)["rows"]] == [2021, 2022, 2023, 2024, 2025]
    assert not data(answer)["rows"][0]["cells"]
    assert answer.usage["model_calls"] == 0 and answer.model is None
    assert not any("claim " in gap for gap in answer.gaps)
    assert service.corpus.run(answer.id)["mode"] == "trend"
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_negative_profit_zero_ocf_and_gross_fcf_survive_without_missing_zero(service):
    add(
        service,
        "cash",
        values=[
            ("revenue", "100"),
            ("net_income", "-20"),
            ("operating_cash_flow", "0"),
            ("cash_capex", "10"),
            ("capex_sale_incentive_receipts", "4"),
        ],
    )
    answer = service.financial_trend(request(years=3))
    row = data(answer)["rows"][-1]
    ledger = {e["id"]: e for e in answer.evidence}
    assert Decimal(ledger[row["cells"]["net_income"]]["value"]) == -20
    assert Decimal(ledger[row["cells"]["operating_cash_flow"]]["value"]) == 0
    assert Decimal(ledger[row["cells"]["free_cash_flow"]]["value"]) == -10
    assert "operating_cash_flow" not in row["missing"]
    assert len(ledger[row["cells"]["free_cash_flow"]]["inputs"]) == 2


def test_newer_version_must_not_mix_older_cash_or_profit(service):
    add(service, "newer", values=[("revenue", "777")])
    row = data(service.financial_trend(request()))["rows"][-1]
    assert row["cells"]["revenue"] == "newer-0"
    assert "net_income" not in row["cells"] and "operating_cash_flow" not in row["cells"]


def test_conflicting_latest_metric_is_withheld_instead_of_older_fallback(service):
    add(service, "conflict", values=[("revenue", "777"), ("revenue", "888"), ("net_income", "10")])
    row = data(service.financial_trend(request()))["rows"][-1]
    assert "revenue" not in row["cells"] and row["cells"]["net_income"] == "conflict-2"
    assert "冲突" in row["issues"][0]


def test_short_and_future_annual_inputs_are_excluded(service):
    add(service, "short", start="2025-07-01", values=[("revenue", "777")])
    add(service, "future", filed="2026-10-09", values=[("revenue", "888")])
    answer = service.financial_trend(request())
    assert all(
        e["filing_id"] not in {"short", "future"} for e in answer.evidence if e["kind"] == "fact"
    )


@pytest.mark.parametrize("tamper", ["metric", "year", "missing", "fcf", "scope"])
def test_resealed_trend_scope_tampering_is_rejected(service, tamper):
    add(
        service,
        "cash",
        values=[("revenue", "100"), ("operating_cash_flow", "20"), ("cash_capex", "5")],
    )
    answer = service.financial_trend(request())
    package = copy.deepcopy(service.export_package(answer.id))
    view = package["research"]["trace"][0]["result"]
    row = view["rows"][-1]
    if tamper == "metric":
        row["cells"]["net_income"] = row["cells"]["revenue"]
        row["missing"].remove("net_income")
    elif tamper == "year":
        row["fiscal_year"] -= 1
    elif tamper == "missing":
        row["missing"].append("revenue")
    elif tamper == "fcf":
        row["cells"]["free_cash_flow"] = row["cells"]["operating_cash_flow"]
    else:
        view["ticker"] = "MSFT"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    report = audit_package(package)
    assert report["status"] == "invalid"
    assert any(c["code"] == "financial_trend" and c["status"] == "fail" for c in report["checks"])


def test_local_sources_are_not_reacquired(service):
    service.discovery.prepare_financial_trend = lambda *args: pytest.fail(
        "unexpected source request"
    )
    answer = service.financial_trend(request(refresh_sources=False))
    assert any("未联网" in gap for gap in answer.gaps)


def test_free_question_uses_tools_without_gateway_or_duplicate_save(service):
    req = ResearchRequest(
        language="zh",
        ticker="AAPL",
        question="过去五年营收、利润和现金流趋势怎么样？",
        mode="agent",
        as_of=DAY,
        auto_acquire=True,
    )
    assert (
        service.research_plan(req)["kind"] == "trend"
        and not service.research_plan(req)["uses_model"]
    )
    service.discovery.prepare = lambda *args: pytest.fail("ordinary preparation must not run")
    answer = service.research(req)
    assert (
        answer.mode == "trend"
        and answer.question == req.question
        and answer.usage["model_calls"] == 0
    )
    assert audit_package(service.export_package(answer.id))["status"] == "consistent"


def test_api_validation_export_and_offline_audit(service):
    with TestClient(create_app(service)) as client:
        assert (
            client.post("/api/financial/trend", json={"ticker": "AAPL", "years": 4}).status_code
            == 422
        )
        response = client.post(
            "/api/financial/trend", json=request(years=3).model_dump(mode="json")
        )
        assert response.status_code == 200
        run = response.json()
        assert len(data(type("Answer", (), {"trace": run["trace"]})())["rows"]) == 3
        assert client.get("/api/runs/" + run["id"] + "/audit").json()["status"] == "consistent"
        assert (
            client.get("/api/capabilities?ticker=0700.HK").json()["financial_trend"]["enabled"]
            is False
        )


def test_acquisition_retains_completed_report_on_later_failure(tmp_path, monkeypatch):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    filing = Filing(
        id="AAPL:retained",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-26-000001",
        form="10-K",
        filed="2026-02-01",
        period_end="2025-09-27",
        source_url="https://www.sec.gov/Archives/retained.htm",
        content_sha256="a" * 64,
    )

    def interrupted(*args, **kwargs):
        kwargs["on_entry"]({"filing": filing.model_dump(mode="json"), "facts": [], "passages": []})
        raise httpx.ConnectError("private-auth-example")

    monkeypatch.setattr("filingcopilot.discovery.acquire_company", interrupted)
    report = service.discovery.prepare_financial_trend("AAPL", DAY, 5)
    assert not report["checked"] and report["acquired"]
    assert any(f["id"] == filing.id for f in service.corpus.catalog(DAY, "AAPL"))
    assert "private-auth-example" not in str(report)
    assert not getattr(service.discovery, "quarter_checks", {})


def test_trend_source_window_caches_are_separate(tmp_path, monkeypatch):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    calls = []

    def acquired(*args, **kwargs):
        calls.append(kwargs["years"])
        return []

    monkeypatch.setattr("filingcopilot.discovery.acquire_company", acquired)
    service.discovery.prepare_financial_trend("AAPL", DAY, 3)
    service.discovery.prepare_financial_trend("AAPL", DAY, 3)
    service.discovery.prepare_financial_trend("AAPL", DAY, 5)
    assert calls == [2, 3]


def test_actual_acquisition_loop_invokes_retention_before_next_document(monkeypatch):
    import json

    from filingcopilot.domain import DEFAULT_DOMAIN
    from filingcopilot.ingestion import acquire_company

    submissions = {
        "cik": 320193,
        "tickers": ["AAPL"],
        "filings": {
            "recent": {
                "form": ["10-K", "10-K"],
                "filingDate": ["2025-10-31", "2024-11-01"],
                "reportDate": ["2025-09-27", "2024-09-28"],
                "accessionNumber": ["0000320193-25-000079", "0000320193-24-000123"],
                "primaryDocument": ["latest.htm", "older.htm"],
            }
        },
    }

    class Fetcher:
        def fetch(self, url, *args, **kwargs):
            if "submissions" in url:
                return json.dumps(submissions).encode()
            if "companyfacts" in url:
                return json.dumps({"cik": 320193, "facts": {}}).encode()
            if url.endswith("older.htm"):
                raise httpx.ConnectError("interrupted older report")
            return b"<html><p>Annual source</p></html>"

    completed = []
    with pytest.raises(httpx.ConnectError):
        acquire_company(
            Fetcher(), "AAPL", years=2, domain=DEFAULT_DOMAIN, as_of=DAY, on_entry=completed.append
        )
    assert len(completed) == 1 and completed[0]["filing"]["period_end"] == "2025-09-27"
