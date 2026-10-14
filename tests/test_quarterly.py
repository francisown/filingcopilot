import hashlib
import json
import os
from datetime import date
from decimal import Decimal

import httpx
import pytest

from filingcopilot.audit import audit_package
from filingcopilot.ingestion import SecFetcher, acquire_company, extract_facts
from filingcopilot.models import Claim, Filing, Passage, ResearchRequest, Submission
from filingcopilot.quarterly import stock_request_scope
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def quarter_filing(ticker="AAPL", end="2026-06-27"):
    return Filing(
        id=ticker + ":quarter",
        ticker=ticker,
        company="Quarter fixture",
        cik="0000320193",
        accession="quarter",
        form="10-Q",
        filed="2026-07-30",
        period_end=end,
        source_url="https://www.sec.gov/Archives/quarter.htm",
        content_sha256="a" * 64,
    )


def source():
    # A non-calendar fiscal year, 13-week quarter, nine-month YTD and prior comparatives.
    rows = [
        ("2026-03-29", "2026-06-27", 120),
        ("2025-03-30", "2025-06-28", 100),
        ("2025-09-28", "2026-06-27", 340),
        ("2024-09-29", "2025-06-28", 300),
        ("2025-06-29", "2025-09-27", 999),  # unrelated period in this filing
        ("2025-09-28", "2026-09-26", 888),  # future/annual
    ]
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"accn": "quarter", "form": "10-Q", "start": s, "end": e, "val": v}
                            for s, e, v in rows
                        ]
                    }
                }
            }
        }
    }


def test_quarter_extraction_keeps_quarter_and_ytd_distinct():
    facts = extract_facts(source(), quarter_filing())
    assert {f.value for f in facts} == {Decimal(120), Decimal(100), Decimal(340), Decimal(300)}
    assert all(f.start and f.end <= date(2026, 6, 27) for f in facts)


def prepared(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    filing = quarter_filing()
    service.corpus.ingest(filing, extract_facts(source(), filing), [])
    service.discovery.prepare_quarterly = lambda *_: {"found": True, "warning": None}
    return service


def test_annual_query_never_uses_quarter_or_ytd(tmp_path):
    tools = prepared(tmp_path).tools(date(2026, 10, 8))
    assert not tools.get_financial_fact("AAPL", "revenue", 2026)["found"]


def test_quarter_yoy_handles_52_week_dates_and_rejects_ytd_mix(tmp_path):
    tools = prepared(tmp_path).tools(date(2026, 10, 8))
    current = tools.get_quarterly_fact("AAPL", "revenue", "2026-06-27")["evidence"]
    prior = tools.get_quarterly_fact("AAPL", "revenue", "2025-06-28")["evidence"]
    ytd = tools.get_quarterly_fact("AAPL", "revenue", "2025-06-28", "ytd")["evidence"]
    result = tools.calculate("growth", current["id"], prior["id"])["evidence"]
    assert Decimal(result["value"]) == 20
    with pytest.raises(ValueError, match="comparable"):
        tools.calculate("growth", current["id"], ytd["id"])


def test_latest_quarter_is_available_without_llm_and_auditable(tmp_path):
    service = prepared(tmp_path)
    request = ResearchRequest(language="zh", question="最新季度营业收入同比怎么样？", ticker="AAPL")
    assert service.research_plan(request)["ready"]
    answer = service.research(request)
    assert answer.status == "answered" and answer.usage["model_calls"] == 0
    assert any("单季度" in c["text"] and "20.00%" in c["text"] for c in answer.answer)
    assert not any("FY2026" in c["text"] for c in answer.answer)
    assert (
        audit_package(service.export_package(answer.id), corpus=service.corpus)["status"]
        == "consistent"
    )


def test_single_quarter_word_routes_to_actual_quarter_not_annual(tmp_path):
    service = prepared(tmp_path)
    request = ResearchRequest(language="zh", question="最新单季营业收入同比怎么样？", ticker="AAPL")
    assert service.research_plan(request)["kind"] == "quarterly"
    answer = service.research(request)
    assert answer.usage["model_calls"] == 0
    assert any("单季度" in c["text"] and "20.00%" in c["text"] for c in answer.answer)
    assert not any("FY2025" in c["text"] for c in answer.answer)


def test_stock_prompt_prioritizes_requested_single_quarter(tmp_path):
    from filingcopilot.agent import ResearchAgent

    class Gateway:
        def complete(self, messages, schemas, review=False):
            assert not review
            context = json.loads(messages[1]["content"])
            assert context["requested_scope"]["primary"] == "quarter"
            assert "Use the supplied annual overview first" not in messages[0]["content"]
            assert context["quarterly_overview"]["comparisons"]
            return {"message": {"content": ""}}

    service = prepared(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    tools.market = None
    answer = ResearchAgent(service.settings, Gateway()).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？重点看最新单季盈利和现金投入。", ticker="AAPL"),
        tools,
    )
    assert not any(t["stage"] == "model_error" for t in answer.trace)


def test_stock_plan_keeps_full_preparation_for_explicit_quarter(tmp_path):
    service = prepared(tmp_path)
    request = ResearchRequest(language="zh", question="苹果股票怎么样？重点看最新单季盈利。", ticker="AAPL")
    assert service.research_plan(request)["kind"] == "stock"


@pytest.mark.parametrize(
    "question,primary,explicit",
    [
        ("股票怎么样？", "annual", False),
        ("重点看最新单季盈利和累计现金投入", "quarter", True),
        ("最新季度收入", "quarter", True),
        ("最新财季收入", "quarter", True),
        ("latest quarter profitability", "quarter", True),
        ("重点看年内累计盈利", "ytd", True),
        ("重点看YTD盈利", "ytd", True),
        ("重点看TTM盈利，单季作补充", "ttm", True),
        ("FY2025经营怎么样？最新季度作补充", "annual", True),
    ],
)
def test_explicit_stock_scope_follows_first_requested_financial_window(question, primary, explicit):
    assert stock_request_scope(question) == {"primary": primary, "explicit": explicit}


def test_named_fiscal_quarter_is_not_silently_replaced_by_latest(tmp_path):
    service = prepared(tmp_path)
    plan = service.research_plan(
        ResearchRequest(language="zh", question="第三财季营业收入是多少？", ticker="AAPL")
    )
    assert not plan["ready"] and "指定历史财季" in plan["message"]


def test_missing_prior_margin_has_no_comparison_draft(tmp_path):
    service = prepared(tmp_path)
    content = source()
    row = content["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]
    content["facts"]["us-gaap"]["OperatingIncomeLoss"] = {"units": {"USD": [{**row, "val": 24}]}}
    service.corpus.ingest(quarter_filing(), extract_facts(content, quarter_filing()), [])
    context = service.tools(date(2026, 10, 8)).get_quarterly_overview("AAPL")
    assert not any("利润率" in c["statement"] for c in context["comparisons"])
    assert any(e.get("operation") == "ratio" for e in context["evidence"])


@pytest.mark.parametrize("primary", ["quarter", "ytd"])
def test_narrow_quarter_comparisons_bind_both_margins_and_pass_verification(tmp_path, primary):
    service = prepared(tmp_path)
    content = source()
    rows = content["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    content["facts"]["us-gaap"]["OperatingIncomeLoss"] = {
        "units": {
            "USD": [
                {**r, "val": r["val"] * (0.2 if i % 2 == 0 else 0.1)} for i, r in enumerate(rows)
            ]
        }
    }
    service.corpus.ingest(quarter_filing(), extract_facts(content, quarter_filing()), [])
    tools = service.tools(date(2026, 10, 8))
    context = tools.get_quarterly_overview("AAPL", primary)
    margin = next(c for c in context["comparisons"] if "营业利润率" in c["statement"])
    assert len(margin["evidence_ids"]) == 2
    claims = [Claim(kind="analysis", **c) for c in context["comparisons"]]
    accepted, errors = verify_submission(
        Submission(claims=claims),
        tools,
        "季度经营表现",
        focus_ticker="AAPL",
        semantic_review=lambda q, rows: {
            "verdicts": [
                {"index": r["index"], "supported": True, "reason": "controlled approval"}
                for r in rows
            ]
        },
    )
    assert len(accepted) == len(claims) and not errors
    assert ("单季度" if primary == "quarter" else "年内累计") in margin["statement"]


def test_annual_only_stock_draft_cannot_finish_requested_quarter(tmp_path):
    from test_stock_research import StockGateway

    from filingcopilot.agent import ResearchAgent

    service = prepared(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    tools.market = None
    answer = ResearchAgent(Settings(home=tmp_path, max_rounds=1), StockGateway()).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？重点看最新单季盈利。", ticker="AAPL"), tools
    )
    assert any(
        "requested latest single-quarter" in finding
        for row in answer.trace
        if row["stage"] == "verify"
        for finding in row["findings"]
    )


def test_latest_annual_still_selects_annual_after_quarter_ingested(tmp_path):
    service = prepared(tmp_path)
    answer = service.research(
        ResearchRequest(language="zh", question="最新年度营收是多少？", ticker="AAPL", mode="reference")
    )
    assert answer.status == "answered"
    assert "FY2025" in answer.answer[0]["text"]


def test_old_annual_package_still_recomputes(tmp_path):
    service = prepared(tmp_path)
    answer = service.research(
        ResearchRequest(language="zh", question="苹果 FY2025 营收同比", ticker="AAPL", mode="reference")
    )
    assert all("FY2025" in c["text"] for c in answer.answer)
    assert (
        audit_package(service.export_package(answer.id), corpus=service.corpus)["status"]
        == "consistent"
    )


def test_ytd_query_does_not_return_quarter(tmp_path):
    service = prepared(tmp_path)
    answer = service.research(
        ResearchRequest(language="zh", question="最新季报年内累计营收及同比？", ticker="AAPL")
    )
    assert answer.status == "answered"
    assert all("年内累计" in c["text"] for c in answer.answer)
    assert any("13.33%" in c["text"] for c in answer.answer)


def test_no_prior_comparison_is_partial_instead_of_silently_complete(tmp_path):
    service = prepared(tmp_path)
    content = source()
    rows = content["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    rows[:] = [r for r in rows if r["end"].startswith("2026")]
    service.corpus.ingest(quarter_filing(), extract_facts(content, quarter_filing()), [])
    answer = service.research(ResearchRequest(language="zh", question="最新季度营收及同比？", ticker="AAPL"))
    assert answer.status == "partial" and answer.answer
    assert any("同比" in gap for gap in answer.gaps)


def test_quarterly_acquisition_uses_publication_cutoff_and_latest_period(tmp_path):
    class Fetcher:
        def fetch(self, url, offline=False, max_age=None):
            if "/submissions/" in url:
                return json.dumps(
                    {
                        "cik": 320193,
                        "tickers": ["AAPL"],
                        "filings": {
                            "recent": {
                                "form": ["10-Q", "10-Q", "10-Q/A", "10-Q"],
                                "accessionNumber": [
                                    "0000320193-26-000004",
                                    "0000320193-26-000003",
                                    "0000320193-26-000002",
                                    "0000320193-26-000001",
                                ],
                                "filingDate": [
                                    "2026-10-30",
                                    "2026-07-30",
                                    "2026-08-01",
                                    "2026-04-30",
                                ],
                                "reportDate": [
                                    "2026-09-26",
                                    "2026-06-27",
                                    "2026-03-28",
                                    "2026-03-28",
                                ],
                                "primaryDocument": [
                                    "future.htm",
                                    "current.htm",
                                    "old-amend.htm",
                                    "prior.htm",
                                ],
                            },
                            "files": [],
                        },
                    }
                ).encode()
            if "/companyfacts/" in url:
                return json.dumps({"cik": 320193, "facts": {}}).encode()
            return b"<html></html>"

    entries = acquire_company(
        Fetcher(), "AAPL", years=1, as_of=date(2026, 10, 8), forms=("10-Q", "10-Q/A")
    )
    assert len(entries) == 1 and entries[0]["filing"]["period_end"] == "2026-06-27"
    historical = acquire_company(
        Fetcher(), "AAPL", years=1, as_of=date(2026, 5, 1), forms=("10-Q", "10-Q/A")
    )
    assert len(historical) == 1 and historical[0]["filing"]["period_end"] == "2026-03-28"


def test_network_failure_keeps_quarter_and_reports_unconfirmed_freshness(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    filing = quarter_filing()
    service.corpus.ingest(filing, extract_facts(source(), filing), [])

    class Failed:
        def fetch(self, *args, **kwargs):
            raise httpx.ConnectError("fixture disconnection")

    service.discovery.fetcher = Failed()
    answer = service.research(ResearchRequest(language="zh", question="最新季度营收？", ticker="AAPL"))
    assert answer.status == "partial" and "单季度" in answer.answer[0]["text"]
    assert any("检查未完成" in gap and "缓存" in gap for gap in answer.gaps)


def test_refresh_retains_raw_legacy_offsets_and_version_hashes(tmp_path, monkeypatch):
    service = prepared(tmp_path)
    cache = tmp_path / "sec-cache"
    cache.mkdir(exist_ok=True)
    html = b"<html></html>"
    filing = quarter_filing().model_copy(
        update={"content_sha256": hashlib.sha256(html).hexdigest()}
    )
    service.corpus.ingest(filing, extract_facts(source(), filing), [])
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{filing.cik}.json"
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")
    old_bytes = json.dumps(source()).encode()
    path.write_bytes(old_bytes)
    (cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")).write_bytes(html)
    answer = service.research(ResearchRequest(language="zh", question="最新季度营收及同比？", ticker="AAPL"))
    package = service.export_package(answer.id)
    changed = source()
    changed["facts"]["us-gaap"]["Revenues"]["units"]["USD"].insert(0, {"val": 999})
    refreshed = json.dumps(changed).encode()
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: httpx.Response(200, content=refreshed, request=httpx.Request("GET", url)),
    )
    os.utime(path, (1, 1))
    fetcher = SecFetcher(cache, "Fixture test@example.invalid")
    assert fetcher.fetch(url, max_age=3600) == refreshed
    assert (
        cache / "raw" / (hashlib.sha256(old_bytes).hexdigest() + ".cache")
    ).read_bytes() == old_bytes
    assert audit_package(package, source_cache=cache)["status"] == "consistent"
    # A tampered response under a valid old filename must not be accepted as that version.
    (cache / "raw" / (hashlib.sha256(old_bytes).hexdigest() + ".cache")).write_bytes(refreshed)
    assert audit_package(package, source_cache=cache)["status"] == "invalid"


def test_unsupported_historical_quarter_is_caught_before_research(tmp_path):
    service = prepared(tmp_path)
    request = ResearchRequest(language="zh", question="FY2026Q1 营收是多少？", ticker="AAPL")
    assert not service.research_plan(request)["ready"]
    assert service.research_plan(request)["suggestions"]


def test_quarter_growth_cannot_use_annual_comparison(tmp_path):
    tools = prepared(tmp_path).tools(date(2026, 10, 8))
    quarter = tools.get_quarterly_fact("AAPL", "revenue", "2026-06-27")["evidence"]
    annual = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    with pytest.raises(ValueError, match="comparable"):
        tools.calculate("growth", quarter["id"], annual["id"])


def test_segment_growth_ranking_requires_more_than_a_numeric_table(tmp_path):
    service = prepared(tmp_path)
    filing = quarter_filing()
    passage = Passage(
        id="table",
        filing_id=filing.id,
        ticker="AAPL",
        section="Operating income",
        text="Operating Income North America 7517 9123 International 1494 1717 AWS 10160 16621",
        source_url=filing.source_url,
        locator="numeric table fixture",
    )
    service.corpus.ingest(filing, extract_facts(source(), filing), [passage])
    tools = service.tools(date(2026, 10, 8))
    tools.ledger[passage.id] = service.corpus.evidence(passage.id, tools.as_of)
    accepted, errors = verify_submission(
        Submission(
            claims=[
                Claim(kind="narrative", evidence_ids=[passage.id], statement="AWS增长幅度最大。")
            ]
        ),
        tools,
        "最新季报营业利润的增长原因",
        semantic_review=lambda q, claims: {
            "verdicts": [{"index": 0, "supported": True, "reason": "fixture approval"}]
        },
        focus_ticker="AAPL",
    )
    assert not accepted and any("ranking" in e for e in errors)


def test_stock_agent_receives_quarter_and_does_not_report_it_missing(tmp_path):
    from filingcopilot.agent import ResearchAgent

    class Gateway:
        def complete(self, messages, tools, review=False):
            payload = json.loads(messages[1]["content"])
            if review:
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "verdicts": [
                                    {
                                        "index": c["index"],
                                        "supported": True,
                                        "reason": "fixture approval",
                                    }
                                    for c in payload["claims"]
                                ]
                            }
                        )
                    },
                    "usage": {},
                }
            overview = payload["quarterly_overview"]
            assert overview["period_end"] == "2026-06-27"
            growth = next(e for e in overview["evidence"] if e.get("operation") == "growth")
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "submit",
                            "type": "function",
                            "function": {
                                "name": "submit_answer",
                                "arguments": json.dumps(
                                    {
                                        "claims": [
                                            {
                                                "kind": "analysis",
                                                "statement": "最新已披露季度营收同比增长，但买入价格仍需估值证据。",
                                                "evidence_ids": [growth["id"]],
                                            }
                                        ],
                                        "gaps": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
                "usage": {},
            }

    service = prepared(tmp_path)
    tools = service.tools(date(2026, 10, 8))
    tools.market = None
    answer = ResearchAgent(service.settings, Gateway()).run(
        ResearchRequest(language="zh", question="苹果股票怎么样？", ticker="AAPL"), tools
    )
    assert answer.answer and any("已补充" in g and "10-Q" in g for g in answer.gaps)
    assert not any("尚未补齐知悉日期前的最新季报" in g for g in answer.gaps)


def test_generic_margin_with_operating_income_query_is_delivered(tmp_path):
    service = prepared(tmp_path)
    data = source()
    data["facts"]["us-gaap"]["OperatingIncomeLoss"] = {
        "units": {
            "USD": [
                {**row, "val": row["val"] / 5}
                for row in data["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
            ]
        }
    }
    filing = quarter_filing()
    service.corpus.ingest(filing, extract_facts(data, filing), [])
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="AAPL 最近季度营业收入、营业利润同比和利润率？",
            ticker="AAPL",
            mode="reference",
            as_of="2026-10-08",
        )
    )
    assert any(
        e.get("operation") == "ratio"
        and [i["metric"] for i in e["inputs"]] == ["operating_income", "revenue"]
        and e["id"] in c["evidence_ids"]
        for c in answer.answer
        for e in answer.evidence
    ), "requested operating profit margin was silently omitted"


def test_missing_current_margin_cannot_be_completed_by_prior_margin(tmp_path):
    service = prepared(tmp_path)
    data = source()
    data["facts"]["us-gaap"]["OperatingIncomeLoss"] = {
        "units": {
            "USD": [
                {**r, "val": r["val"] / 5}
                for r in data["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
            ]
        }
    }
    data["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["val"] = 0
    filing = quarter_filing()
    service.corpus.ingest(filing, extract_facts(data, filing), [])
    answer = service.research(
        ResearchRequest(
            language="zh",
            question="AAPL 最近季度营业利润和利润率？",
            ticker="AAPL",
            mode="reference",
            as_of="2026-10-08",
        )
    )
    assert answer.status == "partial"
    assert any("营业利润率" in g and "不能以上年利润率代替" in g for g in answer.gaps)
