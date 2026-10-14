import hashlib
import json
from datetime import date

import pytest

from filingcopilot.audit import audit_package
from filingcopilot.ingestion import extract_passages
from filingcopilot.models import Claim, Filing, ResearchRequest, Submission
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
from filingcopilot.verification import verify_submission

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


class ForeignFetcher:
    def __init__(self, cache):
        self.cache = cache
        self.calls = []

    def fetch(self, url, offline=False, max_age=None):
        self.calls.append(url)
        if "company_tickers" in url:
            value = {"0": {"cik_str": 1046179, "ticker": "TSM", "title": "TSMC"}}
        elif "/submissions/" in url:
            value = {
                "cik": "0001046179",
                "tickers": ["TSM"],
                "filings": {
                    "recent": {
                        "form": ["20-F", "20-F", "6-K"],
                        "filingDate": ["2026-04-16", "2025-04-17", "2026-07-20"],
                        "reportDate": ["2025-12-31", "2024-12-31", "2026-06-30"],
                        "accessionNumber": [
                            "0001628280-26-025362",
                            "0001193125-25-083423",
                            "0001628280-26-099999",
                        ],
                        "primaryDocument": ["tsm-20251231.htm", "tsm-20241231.htm", "q2.htm"],
                    },
                    "files": [],
                },
            }
        elif "/companyfacts/" in url:
            raise AssertionError(
                "Annual-text preparation must not silently select USD translations"
            )
        else:
            value = None
        content = (
            json.dumps(value).encode()
            if value
            else (
                '<html><p>Item 3. Risk Factors</p><div id="risk"><p>'
                + "Competition and export restrictions may affect our semiconductor business. " * 4
                + "</p></div></html>"
            ).encode()
        )
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")).write_bytes(content)
        return content


@pytest.fixture
def foreign_service(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.discovery.fetcher = ForeignFetcher(tmp_path / "sec-cache")
    return service


def test_foreign_annual_originals_are_researchable_and_source_audited(foreign_service):
    service = foreign_service
    with service.corpus.connect() as db:
        initial_facts = db.execute("SELECT count(*) FROM facts").fetchone()[0]
    prepared = service.discovery.prepare("TSM", date(2026, 10, 9))
    assert [f["form"] for f in prepared["filings"]] == ["20-F", "20-F"]
    assert prepared["annual_text_only"] is True
    with service.corpus.connect() as db:
        assert db.execute("SELECT count(*) FROM facts").fetchone()[0] == initial_facts
    answer = service.research(
        ResearchRequest(
            question="FY2025 年报披露了哪些主要风险？",
            ticker="TSM",
            as_of=date(2026, 10, 9),
            mode="reference",
            auto_acquire=True,
        )
    )
    assert answer.answer and all(c["kind"] == "excerpt" for c in answer.answer)
    package = service.export_package(answer.id)
    report = audit_package(
        package, corpus=service.corpus, source_cache=service.settings.home / "sec-cache"
    )
    assert report["status"] == "consistent"
    assert all(e["filing"]["form"] == "20-F" for e in answer.evidence)


def test_foreign_annual_as_of_excludes_unpublished_filing(foreign_service):
    prepared = foreign_service.discovery.prepare("TSM", date(2026, 4, 15))
    assert [f["period_end"] for f in prepared["filings"]] == ["2024-12-31"]
    assert foreign_service.discovery.refresh_annual("TSM", date(2026, 4, 15))["checked"]
    assert all("q2.htm" not in url for url in foreign_service.discovery.fetcher.calls)


def test_foreign_rag_defaults_to_latest_annual_but_retains_explicit_history(foreign_service):
    service = foreign_service
    service.discovery.prepare("TSM", date(2026, 10, 9))
    tools = service.tools(date(2026, 10, 9))
    current = tools.search_passages("competition", ticker="TSM", lexical_only=True)
    assert current["hits"] and all(
        tools.ledger[h["passage"]["id"]]["filing"]["period_end"] == "2025-12-31"
        for h in current["hits"]
    )
    historical = tools.search_passages(
        "competition", ticker="TSM", fiscal_year=2024, lexical_only=True
    )
    assert historical["hits"] and all(
        tools.ledger[h["passage"]["id"]]["filing"]["period_end"] == "2024-12-31"
        for h in historical["hits"]
    )


@pytest.mark.parametrize(
    "question",
    [
        "最新季度营收是多少？",
        "TTM 营业利润率是多少？",
        "股票怎么样？结合经营表现分析。",
    ],
)
def test_foreign_text_scope_blocks_unavailable_financial_features_before_acquisition(
    foreign_service, question
):
    plan = foreign_service.research_plan(
        ResearchRequest(
            question=question,
            ticker="TSM",
            as_of=date(2026, 10, 9),
            mode="reference",
        )
    )
    assert not plan["ready"] and "20-F" in plan["message"]
    assert not foreign_service.discovery.fetcher.calls
    assert any("年报" in q for q in plan["suggestions"])


def test_foreign_text_scope_advertises_only_available_questions(foreign_service):
    caps = foreign_service.capabilities("TSM")
    assert caps["filings_supported"] and caps["annual_only"]
    assert not caps["annual_text_only"]
    assert any("风险" in q for q in caps["questions"])
    assert any("同比" in q for q in caps["questions"])
    assert not any("TTM" in q or "季度" in q for q in caps["questions"])
    assert caps["annual_ads_valuation"] and any(
        "年度" in q and "市盈率" in q for q in caps["questions"]
    )
    assert foreign_service.capabilities("2330.TW")["questions"] == [
        "现在股价是多少？",
        "过去五年股价涨跌和最大回撤是多少？",
        "过去五年股价与 SPY 标普500 ETF 基准比较",
    ]


def test_foreign_annual_positioned_lines_preserve_narrative_but_exclude_financial_grid():
    lines = ["Competition and export restrictions may affect semiconductor demand."] * 4
    lines += ["Please see Item 4. Information on the Company for additional discussion."]

    def line(text, top):
        return f'<div style="line-height:10pt;position:var(--position);top:{top}pt;width:612pt"><span>{text}</span></div>'

    html = (
        '<div><div id="risk"></div>'
        + line("Item 3. Risk Factors", 45)
        + "".join(line(text, 65 + i * 12) for i, text in enumerate(lines))
        + "</div>"
        + "<table><tr><td>Revenue</td><td>2025</td><td>123456</td></tr>"
        + "<tr><td><p>"
        + "Financial table cells " * 15
        + "</p></td><td>999</td></tr></table>"
    ).encode()
    filing = Filing(
        id="TSM:original",
        ticker="TSM",
        company="TSMC",
        cik="0001046179",
        accession="original",
        form="20-F",
        filed="2026-04-16",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/Archives/original.htm",
        content_sha256="a" * 64,
    )
    passages = extract_passages(html, filing)
    assert len(passages) == 1
    assert passages[0].text == " ".join(lines)
    assert passages[0].section == "Item 3. Risk Factors"


def test_resolved_foreign_company_cannot_bypass_question_scope(foreign_service):
    with pytest.raises(ValueError, match="20-F"):
        foreign_service.research(
            ResearchRequest(
                question="最新季度营收是多少？",
                company="TSM",
                as_of=date(2026, 10, 9),
                mode="reference",
                auto_acquire=True,
            )
        )
    assert not any("/Archives/" in url for url in foreign_service.discovery.fetcher.calls)


def test_split_foreign_source_requires_qualifying_continuation_even_if_reviewer_approves(
    foreign_service,
):
    service = foreign_service
    service.discovery.prepare("TSM", date(2026, 10, 9))
    text = "The previous authorization permitted eligible imports without separate licenses. " * 28
    text += "That authorization later expired and a separate annual license now applies. Renewal is not assured."
    html = f"<div>Risk Factors</div><p>{text}</p>".encode()
    filing = Filing(
        id="TSM:context",
        ticker="TSM",
        company="TSMC",
        cik="0001046179",
        accession="context",
        form="20-F",
        filed="2026-04-17",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/Archives/context.htm",
        content_sha256=hashlib.sha256(html).hexdigest(),
    )
    service.corpus.ingest(filing, [], extract_passages(html, filing))
    tools = service.tools(date(2026, 10, 9))
    result = tools.search_passages("authorization", ticker="TSM", k=1, lexical_only=True)
    first = result["hits"][0]["passage"]["id"]
    reviewed = []

    def reviewer(question, claims):
        reviewed.extend(claims)
        return {
            "verdicts": [dict(index=c["index"], supported=True, reason="Fixture") for c in claims]
        }

    approved, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="narrative",
                    statement="The company has authorization permitting imports without separate licenses.",
                    evidence_ids=[first],
                )
            ]
        ),
        tools,
        "TSM authorization status",
        reviewer,
        focus_ticker="TSM",
    )
    assert not approved and any("complete source context" in error for error in errors)
    assert not reviewed
    context_ids = result["hits"][0]["context_ids"]
    approved, errors = verify_submission(
        Submission(
            claims=[
                Claim(
                    kind="narrative",
                    statement="The previous authorization expired; a separate annual license now applies and renewal is not assured.",
                    evidence_ids=context_ids,
                )
            ]
        ),
        tools,
        "TSM authorization status",
        reviewer,
        focus_ticker="TSM",
    )
    assert approved and not errors
    assert any("expired" in e["text"] for e in reviewed[0]["evidence"])
