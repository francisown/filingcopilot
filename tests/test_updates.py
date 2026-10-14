import hashlib
import json
from datetime import date

import httpx
import pytest

from filingcopilot.audit import audit_package, digest
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.updates import _exhibit, acquire_updates, is_updates_question

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


class UpdatesFetcher:
    def __init__(self, cache):
        self.cache = cache
        (cache / "raw").mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.fail = False
        self.exhibit_fail = False
        self.cik = 320193
        self.archive = False
        self.rows = [
            ("2026-10-09", "2026-10-08", "0000320193-26-000109"),
            ("2026-07-31", "2026-07-30", "0000320193-26-000071"),
            ("2026-07-11", "2026-07-10", "0000320193-26-000010"),
            ("2026-07-10", "2026-07-09", "0000320193-26-000009"),
        ]

    def fetch(self, url, max_age=None):
        self.calls.append(url)
        if self.fail or self.exhibit_fail and "ex99" in url:
            raise httpx.ConnectError("private fixture detail must not be surfaced")
        rows = self.rows
        columns = {
            "form": ["8-K"] * len(rows),
            "filingDate": [r[0] for r in rows],
            "reportDate": [r[1] for r in rows],
            "accessionNumber": [r[2] for r in rows],
            "primaryDocument": ["notice.htm"] * len(rows),
        }
        if "/submissions/" in url:
            if "submissions-001" in url:
                data = columns
            else:
                data = {
                    "cik": self.cik,
                    "tickers": ["AAPL"],
                    "filings": {
                        "recent": {k: [] for k in columns} if self.archive else columns,
                        "files": [
                            {
                                "name": "CIK0000320193-submissions-001.json",
                                "filingFrom": "2026-07-01",
                                "filingTo": "2026-10-09",
                            }
                        ]
                        if self.archive
                        else [],
                    },
                }
            content = json.dumps(data).encode()
        elif "ex991" in url:
            content = b"<html><p>Apple announced financial results and continued expanding its services business. The company described strong customer demand for its products and the introduction of new services in its official release.</p></html>"
        else:
            content = b'<html><p>Item 2.02 Results of Operations and Financial Condition</p><p>Apple announced that it released financial results. The official release accompanying this current report provides details of the company operations and recent product announcements.</p><a href="ex991.htm">99.1</a><a href="ex992.htm">99.2</a></html>'
        (self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")).write_bytes(content)
        (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
        return content


@pytest.fixture
def updates_service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.discovery.fetcher = UpdatesFetcher(tmp_path / "sec-cache")
    yield service
    service.discovery.executor.shutdown(wait=True)


def test_updates_day_window_and_exhibit_source_audit(updates_service):
    service = updates_service
    request = ResearchRequest(
        language="zh",
        question="最近有哪些公司公告？", ticker="AAPL", mode="reference", as_of=date(2026, 10, 8)
    )
    result = service.research(request)
    assert result.status == "answered" and len(result.answer) == 2
    assert result.usage["model_calls"] == 0
    assert all(c["kind"] == "excerpt" for c in result.answer)
    assert all(e["filing"]["form"] == "8-K EX-99.1" for e in result.evidence)
    overview = result.trace[-1]["result"]
    assert [e["filed"] for e in overview["events"]] == ["2026-07-31", "2026-07-11"]
    assert overview["events"][0]["event_date"] == "2026-07-30"
    assert not any(
        "ex992" in u or "000109" in u or "000009/" in u for u in service.discovery.fetcher.calls
    )
    package = service.corpus.run_package(result.id)
    audit = audit_package(
        package, corpus=service.corpus, source_cache=service.settings.home / "sec-cache"
    )
    assert audit["checks_failed"] == 0 and audit["raw_sources"]["missing"] == 0
    assert any(c["code"] == "raw_update_metadata" for c in audit["checks"])


def test_updates_no_future_leak_with_cached_later_notice(updates_service):
    service = updates_service
    service.discovery.prepare_updates("AAPL", date(2026, 10, 8))
    earlier = service.tools(date(2026, 7, 30)).get_company_updates("AAPL")
    assert [e["filed"] for e in earlier["events"]] == ["2026-07-11"]
    assert all(e["filed"] <= "2026-07-30" for e in earlier["events"])


def test_historical_submission_arrays(updates_service):
    service = updates_service
    service.discovery.fetcher.archive = True
    acquired = acquire_updates(service.discovery.fetcher, "AAPL", service.domain, date(2026, 10, 8))
    assert len(acquired["entries"]) == 4
    assert any("submissions-001" in u for u in service.discovery.fetcher.calls)


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.test/ex991.htm",
        "../ex991.htm",
        "ex991.htm?x=1",
        "//evil.test/ex991.htm",
        "ex991.htm#x",
        "ex991.txt",
        "https://www.sec.gov/Archives/other/ex991.htm",
    ],
)
def test_exhibit_cannot_leave_bound_accession(href):
    assert (
        _exhibit(
            f'<a href="{href}">99.1</a>'.encode(),
            "https://www.sec.gov/Archives/edgar/data/1/0001/notice.htm",
        )
        is None
    )


def test_network_failure_retains_cache_and_retries(updates_service):
    service = updates_service
    service.discovery.prepare_updates("AAPL", date(2026, 10, 8))
    service.discovery.update_checks.clear()
    service.discovery.fetcher.fail = True
    request = ResearchRequest(
        language="zh",
        question="latest news", ticker="AAPL", mode="reference", as_of=date(2026, 10, 8)
    )
    result = service.research(request)
    assert result.status == "partial" and result.answer
    assert "最新公告检查未完成" in result.gaps[0]
    assert "private" not in result.gaps[0]
    calls = len(service.discovery.fetcher.calls)
    service.research(request)
    assert len(service.discovery.fetcher.calls) > calls


def test_attachment_failure_is_reported_without_discarding_notice(updates_service):
    service = updates_service
    service.discovery.fetcher.exhibit_fail = True
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最近公司消息", ticker="AAPL", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "partial" and result.answer
    assert all(e["filing"]["form"] == "8-K" for e in result.evidence)
    assert any("EX-99.1" in g for g in result.gaps)
    assert not getattr(service.discovery, "update_checks", {})


def test_company_identity_mismatch(updates_service):
    service = updates_service
    service.discovery.fetcher.cik = 1
    acquired = service.discovery.prepare_updates("AAPL", date(2026, 10, 8))
    assert not acquired["checked"] and acquired["warnings"]
    assert not any(
        f["form"].startswith("8-K") for f in service.corpus.catalog(date(2026, 10, 8), "AAPL")
    )


def test_metadata_detects_tampered_public_date(updates_service):
    service = updates_service
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最近公司公告", ticker="AAPL", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    package = service.corpus.run_package(result.id)
    for evidence in package["research"]["evidence"]:
        evidence["filing"]["filed"] = "2026-08-01"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    report = audit_package(package, source_cache=service.settings.home / "sec-cache")
    assert report["status"] == "invalid"
    assert any(
        c["code"] == "raw_update_metadata" and c["status"] == "fail" for c in report["checks"]
    )


def test_news_preflight_support_and_foreign_scope(updates_service):
    service = updates_service
    assert is_updates_question("最近有什么消息？")
    plan = service.research_plan(ResearchRequest(language="zh", question="最新新闻", ticker="AAPL"))
    assert plan["ready"] and plan["kind"] == "updates" and not plan["uses_model"]
    foreign = service.research_plan(ResearchRequest(language="zh", question="最新新闻", ticker="0700.HK"))
    assert foreign["ready"] and foreign["kind"] == "updates"


def test_exhibit_uses_adjacent_filing_table_label():
    html = b'<table><tr><td>99.1</td><td><a href="release.htm">Press release</a></td></tr><tr><td>99.2</td><td><a href="non-gaap.htm">Reconciliation</a></td></tr></table>'
    assert (
        _exhibit(html, "https://www.sec.gov/Archives/edgar/data/1/0001/notice.htm")
        == "https://www.sec.gov/Archives/edgar/data/1/0001/release.htm"
    )


def test_empty_updates_window_returns_actionable_next_step(updates_service):
    service = updates_service
    service.discovery.fetcher.rows = []
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最近公司公告", ticker="AAPL", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "abstained" and not result.answer
    assert "调整知悉日期" in result.gaps[0]
    assert result.trace[-1]["result"]["events"] == []


def test_failed_model_summary_falls_back_to_audited_exact_excerpts(updates_service):
    class FailingGateway:
        def complete(self, *args, **kwargs):
            raise TimeoutError("fixture failure")

    service = updates_service
    service.gateway = FailingGateway()
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最近公司公告", ticker="AAPL", mode="agent", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "partial" and result.answer
    assert all(c["kind"] == "excerpt" for c in result.answer)
    assert "模型摘要未完成" in result.gaps[0]
    assert any(t["stage"] == "model_error" for t in result.trace)
    assert service.audit_run(result.id)["checks_failed"] == 0


def test_agent_receives_update_excerpts_and_short_statement_schema(updates_service):
    class Gateway:
        def complete(self, messages, tools, review=False):
            if review:
                claims = json.loads(messages[1]["content"])["claims"]
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "verdicts": [
                                    {
                                        "index": c["index"],
                                        "supported": True,
                                        "reason": "synthetic entailment fixture",
                                    }
                                    for c in claims
                                ]
                            }
                        )
                    },
                    "usage": {"total_tokens": 10},
                }
            context = json.loads(messages[1]["content"])["company_updates"]
            assert context["events"][0]["excerpts"][0]["text"].startswith("Apple announced")
            schema = next(s for s in tools if s["function"]["name"] == "submit_answer")["function"][
                "parameters"
            ]["properties"]["claims"]
            assert (
                schema["maxItems"] == 5
                and schema["items"]["properties"]["statement"]["maxLength"] == 160
            )
            key = context["events"][0]["evidence_ids"][0]
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
                                                "kind": "narrative",
                                                "evidence_ids": [key],
                                                "statement": "苹果发布财务业绩，并介绍了服务业务的发展。",
                                            }
                                        ],
                                        "gaps": [],
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
                "usage": {"total_tokens": 10},
            }

    service = updates_service
    service.gateway = Gateway()
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最近有哪些公告", ticker="AAPL", mode="agent", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "answered" and result.answer[0]["kind"] == "narrative"
    assert result.usage["model_calls"] == 2
    assert service.audit_run(result.id)["checks_failed"] == 0


class ForeignUpdatesFetcher(UpdatesFetcher):
    def __init__(self, cache):
        super().__init__(cache)
        self.cik = 1046179
        self.rows = [
            ("2026-10-09", "2026-09-30", "0001046179-26-000681"),
            ("2026-10-08", "2026-09-30", "0001046179-26-000680"),
            ("2026-09-01", "2026-09-01", "0001046179-26-000552"),
            ("2026-07-10", "2026-06-30", "0001046179-26-000447"),
        ]
        self.forms = ["6-K"] * len(self.rows)
        self.boilerplate = False

    def fetch(self, url, max_age=None):
        content = super().fetch(url, max_age)
        if "/submissions/" in url:
            data = json.loads(content)
            if "filings" in data:
                data["tickers"] = ["TSM"]
                data["filings"]["recent"]["form"] = [] if self.archive else self.forms
                for archive in data["filings"]["files"]:
                    archive["name"] = "CIK0001046179-submissions-001.json"
            else:
                data["form"] = self.forms
            content = json.dumps(data).encode()
        elif self.boilerplate:
            content = b"<p>Pursuant to the requirements of the Securities Exchange Act of 1934, the registrant has duly caused this report to be signed on its behalf by the undersigned, thereunto duly authorized.</p>"
        elif "ex991" in url:
            content = b"<p>TSMC announced its monthly consolidated revenue. The company described the reporting period, the month-on-month movement and the year-on-year comparison in its official revenue release.</p>"
        else:
            content = b'<p>TSMC announced a dividend adjustment in this official company notice. The distribution covers the declared quarter and the payment date is separately specified in the original announcement.</p><a href="ex991.htm">99.1</a><a href="ex992.htm">99.2</a>'
        (self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")).write_bytes(content)
        (self.cache / "raw" / (hashlib.sha256(content).hexdigest() + ".cache")).write_bytes(content)
        return content


@pytest.fixture
def foreign_updates_service(tmp_path):
    from filingcopilot.domain import Company

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.register_company(
        "TSM", Company(cik="0001046179", name="Taiwan Semiconductor Manufacturing Company Limited")
    )
    service.refresh_domain()
    service.discovery.fetcher = ForeignUpdatesFetcher(tmp_path / "sec-cache")
    yield service
    service.discovery.executor.shutdown(wait=True)


def test_foreign_updates_actual_service_scope_dates_excerpts_and_audit(foreign_updates_service):
    service = foreign_updates_service
    req = ResearchRequest(
        language="zh",
        question="最近有哪些公司公告？",
        ticker="TSM",
        mode="agent",
        as_of=date(2026, 10, 8),
        auto_acquire=True,
    )
    plan = service.research_plan(req)
    assert plan["ready"] and plan["kind"] == "updates" and not plan["uses_model"]
    result = service.research(req)
    assert result.status == "answered" and len(result.answer) == 2
    assert result.usage["model_calls"] == 0
    overview = result.trace[-1]["result"]
    assert "6-K" in overview["scope"] and "8-K" not in overview["scope"]
    assert overview["events"][0]["event_date"] is None
    assert overview["events"][0]["report_date"] == "2026-09-30"
    assert overview["events"][0]["date_basis"] == "sec_report_date"
    assert [e["filed"] for e in overview["events"]] == ["2026-10-08", "2026-09-01"]
    assert "季度财务" not in overview["next_step"]
    assert all(e["filing"]["form"] == "6-K EX-99.1" for e in result.evidence)
    assert not any(
        "681" in u or "447" in u or "ex992" in u or "companyfacts" in u
        for u in service.discovery.fetcher.calls
    )
    assert not any(f["form"].startswith("20-F") for f in service.corpus.catalog(req.as_of, "TSM"))
    report = audit_package(
        service.export_package(result.id),
        source_cache=service.settings.home / "sec-cache",
        trusted_domain=service.domain,
    )
    assert report["status"] == "consistent" and report["raw_sources"]["missing"] == 0
    assert any(
        c["code"] == "raw_update_metadata" and c["status"] == "pass" for c in report["checks"]
    )
    caps = service.capabilities("TSM")
    assert caps["company_updates"] and any("公告" in q for q in caps["questions"])
    assert not service.capabilities("2330.TW")["company_updates"]
    assert not service.research_plan(
        ResearchRequest(language="zh", question="最新季度营业利润是多少？", ticker="TSM")
    )["ready"]


def test_foreign_updates_never_uses_configured_summary_model(foreign_updates_service):
    class ForbiddenGateway:
        def complete(self, *args, **kwargs):
            pytest.fail("6-K excerpt delivery must not call an unvalidated summary model")

    service = foreign_updates_service
    service.gateway = ForbiddenGateway()
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最新 6-K 公告", ticker="TSM", mode="agent", as_of=date(2026, 10, 8)
        )
    )
    assert result.answer and all(c["kind"] == "excerpt" for c in result.answer)
    assert result.usage["model_calls"] == 0


class SummaryGateway:
    def __init__(self, mutation=None, reject=None, fail_review=False, gaps=None):
        self.mutation = mutation
        self.reject = reject
        self.fail_review = fail_review
        self.gaps = gaps or []
        self.calls = []

    def complete(self, messages, tools, review=False):
        self.calls.append((messages, tools, review))
        if review:
            if self.fail_review:
                raise TimeoutError("private fixture error")
            payload = json.loads(messages[1]["content"])
            claims = payload["claims"]
            assert payload["required_indexes"] == [c["index"] for c in claims]
            schema = tools[0]["function"]["parameters"]["properties"]["verdicts"]
            assert schema["items"]["properties"]["index"]["enum"] == payload["required_indexes"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": c["index"],
                                    "supported": c["index"] != self.reject,
                                    "reason": "controlled fixture verdict",
                                }
                                for c in claims
                            ]
                        }
                    )
                },
                "usage": {"total_tokens": 7},
            }
        assert [s["function"]["name"] for s in tools] == ["submit_answer"]
        overview = json.loads(messages[1]["content"])["company_updates"]
        self.overview = overview
        claims = [
            {
                "kind": "narrative",
                "evidence_ids": e["evidence_ids"],
                "statement": "公司发布月度营收公告，并介绍了环比与同比比较。",
            }
            for e in overview["events"]
            if e["evidence_ids"]
        ]
        if self.mutation:
            self.mutation(claims)
        return {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "summary",
                        "type": "function",
                        "function": {
                            "name": "submit_answer",
                            "arguments": json.dumps(
                                {"claims": claims, "gaps": self.gaps}, ensure_ascii=False
                            ),
                        },
                    }
                ],
            },
            "usage": {"total_tokens": 11},
        }


def summary_request(**kwargs):
    return ResearchRequest(
        language="zh",
        question="近期公司公告中文摘要",
        ticker="TSM",
        mode="agent",
        as_of=date(2026, 10, 8),
        **kwargs,
    )


def test_explicit_foreign_summary_uses_review_with_frozen_report_dates(foreign_updates_service):
    service = foreign_updates_service
    gateway = SummaryGateway()
    service.gateway = gateway
    req = summary_request()
    assert service.capabilities("TSM")["company_update_summary"]
    assert not service.capabilities("2330.TW")["company_update_summary"]
    plan = service.research_plan(req)
    assert plan["ready"] and plan["uses_model"] and "事件发生日" in plan["message"]
    result = service.research(req)
    assert result.status == "answered" and len(result.answer) == 2
    assert all(c["verification"] == "semantic_review" for c in result.answer)
    assert result.usage["model_calls"] == 2 and result.usage["total_tokens"] == 18
    assert "NEVER proof of an event" in gateway.calls[0][0][0]["content"]
    assert gateway.overview["events"][0]["event_date"] is None
    assert gateway.overview["events"][0]["report_date"] == "2026-09-30"
    assert not any("companyfacts" in u or "20-F" in u for u in service.discovery.fetcher.calls)
    assert service.audit_run(result.id)["checks_failed"] == 0


@pytest.mark.parametrize("model,mode", [(False, "agent"), (True, "reference")])
def test_summary_without_model_or_agent_delivers_excerpts(foreign_updates_service, model, mode):
    service = foreign_updates_service
    service.gateway = SummaryGateway() if model else None
    req = summary_request().model_copy(update={"mode": mode})
    assert not service.research_plan(req)["uses_model"]
    result = service.research(req)
    assert result.status == "partial" and result.usage["model_calls"] == 0
    assert all(c["kind"] == "excerpt" for c in result.answer)
    assert "Agent 模式" in result.gaps[0]
    assert service.audit_run(result.id)["checks_failed"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c[0].update(statement="Company announced monthly revenue."),
        lambda c: c[0].update(statement="公告" * 81),
        lambda c: c[0].update(kind="analysis"),
        lambda c: c[0].update(evidence_ids=["unobserved"]),
        lambda c: c[0].update(evidence_ids=c[0]["evidence_ids"] + c[1]["evidence_ids"]),
        lambda c: c.append(dict(c[0])),
    ],
)
def test_invalid_summary_falls_back_without_delivering_unchecked_text(
    foreign_updates_service, mutation
):
    service = foreign_updates_service
    gateway = SummaryGateway(mutation=mutation)
    service.gateway = gateway
    result = service.research(summary_request())
    assert result.status == "partial" and result.answer
    assert all(c["kind"] == "excerpt" for c in result.answer)
    assert "模型摘要未完成" in result.gaps[0]
    assert len([call for call in gateway.calls if not call[2]]) == 1
    assert service.audit_run(result.id)["checks_failed"] == 0


def test_invented_summary_number_is_withheld_before_review(foreign_updates_service):
    service = foreign_updates_service
    gateway = SummaryGateway(mutation=lambda c: c[0].update(statement="营收增长了百分之二十。"))
    service.gateway = gateway
    result = service.research(summary_request())
    assert result.status == "partial"
    assert [c["kind"] for c in result.answer] == ["narrative", "excerpt"]
    assert all("百分之二十" not in c["text"] for c in result.answer)
    review = next(c for c in gateway.calls if c[2])
    assert "百分之二十" not in review[0][1]["content"]
    assert len({c["index"] for c in result.answer}) == len(result.answer)
    assert service.audit_run(result.id)["checks_failed"] == 0


def test_rejected_summary_retains_other_reviewed_claim_and_missing_original(
    foreign_updates_service,
):
    service = foreign_updates_service
    service.gateway = SummaryGateway(reject=1)
    result = service.research(summary_request())
    assert result.status == "partial"
    assert [c["kind"] for c in result.answer] == ["narrative", "excerpt"]
    assert any("1 份公告未完成中文摘要" in g for g in result.gaps)
    assert service.audit_run(result.id)["checks_failed"] == 0


def test_summary_reviewer_timeout_never_delivers_generated_text(foreign_updates_service):
    service = foreign_updates_service
    service.gateway = SummaryGateway(fail_review=True)
    result = service.research(summary_request())
    assert all(c["kind"] == "excerpt" for c in result.answer)
    assert any(t["stage"] == "semantic_review_error" for t in result.trace)
    assert "private fixture" not in json.dumps(result.model_dump(mode="json"))
    assert service.audit_run(result.id)["checks_failed"] == 0


@pytest.mark.parametrize(
    "text,rejected",
    [
        ("公司已发放现金股利。", True),
        ("公司批准现金股利并于十月支付。", True),
        ("公司计划于十月支付现金股利。", False),
        ("公司批准现金股利，预计于十月支付。", False),
        ("股利计划支付日期已过，不代表已经实际支付。", False),
    ],
)
def test_notice_plan_does_not_prove_actual_payment_after_scheduled_date(
    foreign_updates_service, text, rejected
):
    from filingcopilot.models import Claim, Submission
    from filingcopilot.verification import verify_submission

    service = foreign_updates_service
    service.discovery.prepare_updates("TSM", date(2026, 10, 8))
    tools = service.tools(date(2026, 10, 8))
    key = tools.get_company_updates("TSM")["events"][0]["evidence_ids"][0]
    tools.ledger[key]["text"] = (
        "The Board approved a cash dividend. The dividend will be paid on October 1, 2026."
    )
    reviews = []

    def review(question, claims):
        reviews.extend(claims)
        return {"verdicts": [{"index": 0, "supported": True, "reason": "fixture"}]}

    claims, errors = verify_submission(
        Submission(claims=[Claim(kind="narrative", statement=text, evidence_ids=[key])]),
        tools,
        "公告中文摘要",
        review,
        "TSM",
    )
    assert bool(claims) is not rejected
    if rejected:
        assert not reviews and any("scheduled payment" in e for e in errors)


def test_complete_summary_does_not_take_unreviewed_model_gaps(foreign_updates_service):
    service = foreign_updates_service
    service.gateway = SummaryGateway(gaps=["本次只覆盖部分新闻。", "没有来源的其他模型断言。"])
    result = service.research(summary_request())
    assert result.status == "answered" and result.gaps == []
    assert service.audit_run(result.id)["checks_failed"] == 0


def test_unreadable_notices_do_not_spend_summary_model_budget(foreign_updates_service):
    service = foreign_updates_service
    service.discovery.fetcher.boilerplate = True
    service.gateway = SummaryGateway()
    result = service.research(summary_request())
    assert result.status == "abstained" and not result.answer
    assert result.usage["model_calls"] == 0 and service.gateway.calls == []
    assert "未调用摘要模型" in result.gaps[0]
    overview = next(t["result"] for t in result.trace if t.get("tool") == "get_company_updates")
    assert overview["events"] and overview["events"][0]["documents"]


def test_foreign_updates_filter_form_and_future_before_download(foreign_updates_service):
    service = foreign_updates_service
    service.discovery.fetcher.forms = ["6-K", "8-K", "6-K/A", "6-K"]
    service.discovery.prepare_updates("TSM", date(2026, 10, 8))
    result = service.tools(date(2026, 10, 8)).get_company_updates("TSM")
    assert [e["filed"] for e in result["events"]] == ["2026-09-01"]
    assert not any("680" in u or "681" in u for u in service.discovery.fetcher.calls)
    older = service.tools(date(2026, 8, 31)).get_company_updates("TSM")
    assert not older["events"]


def test_foreign_updates_does_not_quote_signature_as_company_news(foreign_updates_service):
    service = foreign_updates_service
    service.discovery.fetcher.boilerplate = True
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最近有哪些公告", ticker="TSM", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "abstained" and not result.answer
    assert result.trace[-1]["result"]["events"]
    assert all(not e["evidence_ids"] for e in result.trace[-1]["result"]["events"])
    assert result.gaps and not any("季度财务仍可用" in g for g in result.gaps)


def test_foreign_updates_tampered_report_date_rejected(foreign_updates_service):
    service = foreign_updates_service
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最新公告", ticker="TSM", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    package = service.export_package(result.id)
    for e in package["research"]["evidence"]:
        if "filing" in e:
            e["filing"]["period_end"] = "2026-10-08"
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    report = audit_package(package, source_cache=service.settings.home / "sec-cache")
    assert report["status"] == "invalid"
    assert any(
        c["code"] == "raw_update_metadata" and c["status"] == "fail" for c in report["checks"]
    )


def test_foreign_updates_network_failure_preserves_originals_and_retry(foreign_updates_service):
    service = foreign_updates_service
    service.discovery.prepare_updates("TSM", date(2026, 10, 8))
    service.discovery.update_checks.clear()
    service.discovery.fetcher.fail = True
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最新公告", ticker="TSM", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "partial" and result.answer
    assert any("检查未完成" in g for g in result.gaps)
    assert not any("private fixture" in g for g in result.gaps)
    assert not service.discovery.update_checks
    assert service.audit_run(result.id)["checks_failed"] == 0


def test_foreign_update_parser_labels_notice_and_excludes_table_wrappers():
    from filingcopilot.ingestion import extract_passages
    from filingcopilot.models import Filing

    text = "TSMC announced its monthly consolidated revenue and supplied the reporting period and comparisons with the previous month and the same month of last year."
    html = (
        "<p>"
        + text
        + "</p><div><table><tr><td>Revenue current prior " * 8
        + "</td></tr></table></div>"
    ).encode()
    filing = Filing(
        id="TSM:test",
        ticker="TSM",
        company="TSMC",
        cik="0001046179",
        accession="test",
        form="6-K",
        filed="2026-10-08",
        period_end="2026-09-30",
        source_url="https://www.sec.gov/Archives/test.htm",
        content_sha256=hashlib.sha256(html).hexdigest(),
    )
    ps = extract_passages(html, filing)
    assert len(ps) == 1 and ps[0].text == text
    assert "Foreign issuer" in ps[0].section and "Annual" not in ps[0].section


@pytest.mark.parametrize("question", ["最新 6-K 季度营收是多少？", "6-K TTM 净利润怎么算？"])
def test_foreign_notice_scope_does_not_unlock_quarterly_or_ttm_calculation(
    foreign_updates_service, question
):
    plan = foreign_updates_service.research_plan(
        ResearchRequest(language="zh", question=question, ticker="TSM", mode="reference")
    )
    assert not plan["ready"] and "季度财务计算" in plan["message"]
    assert not foreign_updates_service.discovery.fetcher.calls


def test_foreign_updates_archive_and_identity_binding(foreign_updates_service):
    service = foreign_updates_service
    service.discovery.fetcher.archive = True
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最新公告", ticker="TSM", mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert len(result.answer) == 2
    report = audit_package(
        service.export_package(result.id), source_cache=service.settings.home / "sec-cache"
    )
    assert report["status"] == "consistent" and report["raw_sources"]["missing"] == 0
    service.discovery.update_checks.clear()
    service.discovery.fetcher.cik = 320193
    prepared = service.discovery.prepare_updates("TSM", date(2026, 10, 8))
    assert not prepared["checked"] and prepared["warnings"]


def test_foreign_updates_limit_counts_accessions_not_exhibits(foreign_updates_service):
    service = foreign_updates_service
    fetcher = service.discovery.fetcher
    fetcher.rows = [
        ("2026-10-" + str(i).zfill(2), "2026-09-30", f"0001046179-26-{i:06d}") for i in range(1, 9)
    ]
    fetcher.forms = ["6-K"] * 8
    prepared = service.discovery.prepare_updates("TSM", date(2026, 10, 8))
    assert len(prepared["acquired"]) == 10
    result = service.tools(date(2026, 10, 8)).get_company_updates("TSM")
    assert len(result["events"]) == 5 and sum(len(e["documents"]) for e in result["events"]) == 10
    assert [e["filed"] for e in result["events"]] == [f"2026-10-{i:02d}" for i in range(8, 3, -1)]


def test_foreign_updates_tool_without_market_adapter_keeps_registered_scope(
    foreign_updates_service,
):
    service = foreign_updates_service
    service.discovery.prepare_updates("TSM", date(2026, 10, 8))
    tools = service.tools(date(2026, 10, 8))
    tools.market = None
    result = tools.get_company_updates("TSM")
    assert result["found"] and "6-K" in result["scope"] and len(result["events"]) == 2
    assert all(e["event_date"] is None for e in result["events"])


@pytest.mark.parametrize("foreign", [False, True])
def test_update_refresh_expiry_retains_each_completed_original(
    updates_service, foreign_updates_service, foreign
):
    from filingcopilot.execution import RefreshBudgetExceeded

    service = foreign_updates_service if foreign else updates_service
    ticker = "TSM" if foreign else "AAPL"
    fetcher = service.discovery.fetcher
    original = fetcher.fetch

    def limited(url, max_age=None):
        if ("000552" if foreign else "000010") in url:
            raise RefreshBudgetExceeded()
        return original(url, max_age)

    fetcher.fetch = limited
    result = service.research(
        ResearchRequest(
            language="zh",
            question="最新公告", ticker=ticker, mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert result.status == "partial" and len(result.answer) == 1
    prepared = next(t["result"] for t in result.trace if t["stage"] == "updates_preparation")
    assert not prepared["checked"] and len(prepared["acquired"]) == 2
    assert not getattr(service.discovery, "update_checks", {})
    audit = audit_package(
        service.export_package(result.id), source_cache=service.settings.home / "sec-cache"
    )
    assert audit["status"] == "consistent" and audit["raw_sources"]["missing"] == 0
    fetcher.fetch = original
    retried = service.research(
        ResearchRequest(
            language="zh",
            question="最新公告", ticker=ticker, mode="reference", as_of=date(2026, 10, 8)
        )
    )
    assert retried.status == "answered" and len(retried.answer) == 2
