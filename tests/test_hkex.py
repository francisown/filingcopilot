import hashlib
from datetime import date, datetime, timezone

import pytest

from filingcopilot.audit import audit_package, create_package
from filingcopilot.hkex import HKEXSource, _rows, parse_hkex_announcement
from filingcopilot.models import ResearchAnswer
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def _listing(language="en"):
    label = "Release Time:" if language == "en" else "發放時間:"
    title = "Monthly Return" if language == "en" else "月報表"
    return f"""<table><tbody><tr>
      <td class='release-time'><span>{label}</span>09/10/2026 17:44</td>
      <td class='stock-short-code'><span>Stock Code:</span>00700</td>
      <td><span>Stock Short Name:</span>TENCENT</td>
      <td><div class='headline'>{title}</div><a href='/listedco/listconews/sehk/2026/1009/2026100900649{'_c' if language == 'zh' else ''}.pdf'>{title}</a></td>
    </tr></tbody></table>""".encode()


def test_hkex_english_and_chinese_rows_are_separate():
    source = HKEXSource(stock_code="00700")
    en = _rows(_listing("en"), "en", source, source.search_url)
    zh = _rows(_listing("zh"), "zh", source, source.search_url)
    assert en[0]["language"] == "en"
    assert zh[0]["language"] == "zh"
    assert en[0]["published_at"].tzinfo is not None
    assert en[0]["source_url"] != zh[0]["source_url"]


def test_hkex_announcement_is_official_titled_evidence(tmp_path):
    source = HKEXSource(stock_code="00700")
    row = _rows(_listing("en"), "en", source, source.search_url)[0]
    update = parse_hkex_announcement(
        b"%PDF-1.7 official announcement",
        source,
        "0700.HK",
        row,
        datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    assert update.source_type == "hkex"
    assert update.evidence_level == "hkex_official"
    assert update.text_scope == "hkex_announcement"
    assert update.language == "en"
    assert update.content_sha256 == hashlib.sha256(b"%PDF-1.7 official announcement").hexdigest()

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        service.corpus.ingest_external_update(update)
        overview = service.tools(date(2026, 10, 10)).get_company_updates("0700.HK")
        assert overview["sections"]["hkex_en"][0]["id"] == update.id
        assert overview["sections"]["hkex_zh"] == []
        assert service.tools(date(2026, 10, 8)).get_company_updates("0700.HK")["sections"]["hkex_en"] == []
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_hkex_listing_advertises_market_and_notice_capabilities(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        capabilities = service.capabilities("0700.HK")
        assert capabilities["external_updates"] is True
        assert capabilities["filings_supported"] is False
        assert "HKEXnews" in capabilities["scope"]
        assert any("HKEXnews" in question for question in capabilities["questions"])
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_hkex_announcement_replays_in_frozen_package(tmp_path):
    source = HKEXSource(stock_code="00700")
    row = _rows(_listing("en"), "en", source, source.search_url)[0]
    raw = b"%PDF-1.7 frozen HKEX announcement"
    update = parse_hkex_announcement(
        raw,
        source,
        "0700.HK",
        row,
        datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        service.corpus.ingest_external_update(update)
        raw_path = tmp_path / "external-cache" / "raw" / f"{update.content_sha256}.cache"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        evidence = service.corpus.evidence(update.id, date(2026, 10, 10))
        answer = ResearchAnswer(
            id="hkex-package",
            question="最近公告",
            as_of=date(2026, 10, 10),
            mode="updates",
            status="partial",
            answer=[{
                "index": 0,
                "kind": "excerpt",
                "text": evidence["text"],
                "evidence_ids": [update.id],
                "verification": "exact_source_excerpt",
            }],
            gaps=["PDF正文未在核心安装中抽取"],
            evidence=[evidence],
            trace=[],
            usage={"model_calls": 0},
            elapsed_ms=1,
        )
        package = create_package(answer, service.domain, "0700.HK")
        audit = audit_package(
            package,
            corpus=service.corpus,
            external_cache=tmp_path / "external-cache",
        )
        assert audit["status"] == "consistent"
        assert any(c["code"] == "raw_external_update" for c in audit["checks"])
    finally:
        service.discovery.executor.shutdown(wait=True)
