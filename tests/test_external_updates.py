import hashlib
from datetime import date, datetime, timezone

import pytest

from filingcopilot.audit import audit_package, create_package
from filingcopilot.external_updates import ExternalSource, parse_feed, parse_transcript
from filingcopilot.models import ExternalUpdate, ResearchAnswer
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_rss_public_time_filter_and_source_level():
    source = ExternalSource(
        source_type="news",
        source_name="Issuer feed",
        feed_url="https://issuer.test/feed.xml",
        evidence_level="issuer_news",
    )
    payload = b"""<rss><channel>
      <item><title>Current update</title><link>https://issuer.test/current</link>
      <pubDate>Thu, 08 Oct 2026 12:00:00 GMT</pubDate><description>Demand remained resilient.</description></item>
      <item><title>Future update</title><link>https://issuer.test/future</link>
      <pubDate>Sun, 11 Oct 2026 12:00:00 GMT</pubDate><description>Future text.</description></item>
      <item><title>Undated</title><link>https://issuer.test/undated</link><description>No date.</description></item>
    </channel></rss>"""
    updates = parse_feed(payload, source, "AAPL", datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert len(updates) == 2
    assert all(u.source_type == "news" and u.evidence_level == "issuer_news" for u in updates)
    assert all(u.published_at and u.available_at == u.published_at for u in updates)


def test_transcript_html_without_public_time_is_first_observed_only():
    source = ExternalSource(
        source_type="transcript",
        source_name="Issuer IR",
        feed_url="https://issuer.test/events",
        evidence_level="issuer_transcript",
        format="html_index",
        link_pattern=r"/events/\\d+",
        text_selector=".transcript",
    )
    payload = (
        b"<html><head><title>Q3 Earnings Call Transcript</title></head>"
        b"<body><div class='transcript'>"
        + b"Management discussed demand and capacity. " * 20
        + b"</div></body></html>"
    )
    updates = parse_transcript(
        payload,
        source,
        "AAPL",
        "https://issuer.test/events/3",
        datetime(2026, 10, 9, tzinfo=timezone.utc),
    )
    assert len(updates) == 1
    assert updates[0].published_at is None
    assert updates[0].publication_basis == "first_observed"
    assert updates[0].available_at == updates[0].retrieved_at


def test_external_storage_is_separate_from_official_events(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        source = ExternalSource(
            source_type="news",
            source_name="Issuer feed",
            feed_url="https://issuer.test/feed.xml",
            evidence_level="issuer_news",
        )
        raw = b"issuer news"
        update = ExternalUpdate(
            id="external-test-news",
            ticker="AAPL",
            source_type="news",
            source_name=source.source_name,
            evidence_level=source.evidence_level,
            title="Issuer update",
            text="The issuer described demand in its public update.",
            source_url="https://issuer.test/news/1",
            source_feed_url=source.feed_url,
            source_config=source.model_dump(),
            published_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            available_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            content_sha256=hashlib.sha256(raw).hexdigest(),
            retrieved_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        service.corpus.ingest_external_update(update)
        tools = service.tools(date(2026, 10, 9))
        overview = tools.get_company_updates("AAPL")
        assert overview["events"] == []
        assert overview["sections"]["official"] == []
        assert overview["sections"]["news"][0]["id"] == update.id
        assert service.tools(date(2026, 10, 7)).get_company_updates("AAPL")["sections"]["news"] == []
        assert tools.ledger[update.id]["kind"] == "external_update"
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_external_evidence_package_replays_scope(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        source = ExternalSource(
            source_type="news",
            source_name="Issuer feed",
            feed_url="https://issuer.test/feed.xml",
            evidence_level="issuer_news",
        )
        update = ExternalUpdate(
            id="external-package-news",
            ticker="AAPL",
            source_type="news",
            source_name=source.source_name,
            evidence_level=source.evidence_level,
            title="Issuer update",
            text="The issuer described demand in its public update.",
            source_url="https://issuer.test/news/2",
            source_feed_url=source.feed_url,
            source_config=source.model_dump(),
            published_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            available_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            content_sha256=hashlib.sha256(b"pkg").hexdigest(),
            retrieved_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        service.corpus.ingest_external_update(update)
        tools = service.tools(date(2026, 10, 9))
        evidence = tools.corpus.evidence(update.id, date(2026, 10, 9))
        answer = ResearchAnswer(
            id="run-external",
            question="最新消息",
            as_of=date(2026, 10, 9),
            mode="updates",
            status="partial",
            answer=[{
                "index": 0,
                "kind": "excerpt",
                "text": evidence["text"],
                "evidence_ids": [update.id],
                "verification": "exact_source_excerpt",
            }],
            gaps=["仅外部资料测试"],
            evidence=[evidence],
            trace=[],
            usage={"model_calls": 0},
            elapsed_ms=1,
        )
        package = create_package(answer, service.domain, "AAPL")
        audit = audit_package(package, corpus=service.corpus)
        assert audit["status"] == "consistent"
        assert any(c["code"] == "external_scope" for c in audit["checks"])
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_registered_external_sources_are_visible_in_capabilities(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        capabilities = service.capabilities("MSFT")
        assert capabilities["external_updates"] is True
        assert any("电话会" in question for question in capabilities["questions"])
    finally:
        service.discovery.executor.shutdown(wait=True)
