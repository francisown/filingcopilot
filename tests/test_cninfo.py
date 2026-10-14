import hashlib
from datetime import date, datetime, timezone

import pytest

from filingcopilot.audit import audit_package, create_package
from filingcopilot.cninfo import CNInfoSource, _rows, parse_cninfo_announcement
from filingcopilot.models import ResearchAnswer
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def _announcement_payload():
    return [{
        "announcementId": "1225000000",
        "secCode": "600519",
        "secName": "贵州茅台",
        "announcementTitle": "贵州茅台临时公告",
        "announcementTime": 1791504000000,
        "adjunctUrl": "finalpage/2026-05-28/1225000000.PDF",
    }]


def test_cninfo_rows_keep_chinese_official_metadata():
    source = CNInfoSource(stock_code="600519", column="sse")
    rows = _rows(_announcement_payload(), source)
    assert rows[0]["language"] == "zh"
    assert rows[0]["source_url"].startswith("https://static.cninfo.com.cn/finalpage/")
    assert rows[0]["published_at"].tzinfo is not None


def test_cninfo_announcement_is_separate_evidence_and_replays(tmp_path):
    source = CNInfoSource(stock_code="600519", column="sse")
    row = _rows(_announcement_payload(), source)[0]
    raw = b"%PDF-1.7 frozen CNINFO announcement"
    update = parse_cninfo_announcement(
        raw,
        source,
        "600519.SS",
        row,
        datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    assert update.source_type == "cninfo"
    assert update.evidence_level == "cninfo_official"
    assert update.text_scope == "cninfo_announcement"
    assert update.language == "zh"
    assert update.content_sha256 == hashlib.sha256(raw).hexdigest()

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        service.corpus.ingest_external_update(update)
        raw_path = tmp_path / "external-cache" / "raw" / f"{update.content_sha256}.cache"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        evidence = service.corpus.evidence(update.id, date(2026, 10, 10))
        overview = service.tools(date(2026, 10, 10)).get_company_updates("600519.SS")
        assert overview["sections"]["cninfo"][0]["id"] == update.id
        answer = ResearchAnswer(
            id="cninfo-package",
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
        audit = audit_package(
            create_package(answer, service.domain, "600519.SS"),
            corpus=service.corpus,
            external_cache=tmp_path / "external-cache",
        )
        assert audit["status"] == "consistent"
        assert any(c["code"] == "raw_external_update" for c in audit["checks"])
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_cninfo_listing_advertises_announcement_capability(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    try:
        capabilities = service.capabilities("600519.SS")
        assert capabilities["external_updates"] is True
        assert capabilities["filings_supported"] is False
        assert any("巨潮资讯" in question for question in capabilities["questions"])
    finally:
        service.discovery.executor.shutdown(wait=True)
