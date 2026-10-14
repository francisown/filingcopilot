import copy
from datetime import datetime, timezone

import httpx
import pytest

from filingcopilot.audit import audit_package, create_package, digest
from filingcopilot.market import MarketData
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
DAY = NOW.date()


def research_package(tmp_path, *, backup_close="204", backup_day="2026-10-09"):
    stamps = [int(datetime(2026, 10, day, 20, tzinfo=timezone.utc).timestamp()) for day in (8, 9)]
    primary = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": "AAPL",
                        "currency": "USD",
                        "exchangeName": "NASDAQ",
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": 215,
                        "regularMarketTime": stamps[-1],
                    },
                    "timestamp": stamps,
                    "indicators": {"quote": [{"close": [200, 201], "volume": [1000, 2000]}]},
                }
            ],
        }
    }
    backup = {
        "meta": {
            "symbol": "AAPL",
            "currency": "USD",
            "exchange": "NASDAQ",
            "mic_code": "XNGS",
            "exchange_timezone": "America/New_York",
            "type": "Common Stock",
        },
        "values": [{"datetime": backup_day, "close": backup_close, "volume": "1000"}],
    }

    def respond(request):
        return httpx.Response(
            200, json=backup if request.url.host == "api.twelvedata.com" else primary
        )

    market = MarketData(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        now=lambda: NOW,
        backup_key="fixture-only",
    )
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market = market
    answer = service.research(
        ResearchRequest(question="苹果股价是多少？", ticker="AAPL", as_of=DAY, mode="reference")
    )
    assert answer.answer and answer.evidence
    return create_package(answer, service.domain, "AAPL"), market.cache


@pytest.mark.parametrize(
    "close,day", [("204", "2026-10-09"), ("201", "2026-10-09"), ("204", "2026-10-08")]
)
def test_dual_source_package_replays_both_raw_responses(tmp_path, close, day):
    package, cache = research_package(tmp_path, backup_close=close, backup_day=day)
    report = audit_package(package, market_cache=cache)
    assert report["status"] == "consistent", report["checks"]
    assert report["market_sources"]["checked"] == 2
    assert report["source_validation"] == "matched"
    assert any(
        c["code"] == "raw_market_cross_check" and c["status"] == "pass" for c in report["checks"]
    )


def test_cross_check_has_compact_source_reference_without_full_second_series(tmp_path):
    package, _ = research_package(tmp_path)
    snapshot = package["research"]["evidence"][0]["snapshot"]
    reference = snapshot["cross_check_source"]
    assert reference["ticker"] == "AAPL"
    assert reference["source_url"].startswith("https://api.twelvedata.com/time_series?")
    assert "points" not in reference and "fixture-only" not in str(package)


@pytest.mark.parametrize(
    "mutation", ["value", "percentage", "warning", "raw_backup", "reference_hash"]
)
def test_recomputed_manifest_cannot_hide_cross_check_tampering(tmp_path, mutation):
    package, cache = research_package(tmp_path)
    snapshot = package["research"]["evidence"][0]["snapshot"]
    if mutation == "value":
        snapshot["source_conflicts"][0]["right_value"] = "999"
    elif mutation == "percentage":
        snapshot["source_conflicts"][0]["relative_difference_pct"] = "0"
    elif mutation == "warning":
        snapshot["warnings"][-1] = "两家来源没有差异。"
    elif mutation == "raw_backup":
        path = cache / "raw" / (snapshot["cross_check_source"]["source_sha256"] + ".json")
        path.write_bytes(path.read_bytes().replace(b'"204"', b'"999"'))
    else:
        original = snapshot["cross_check_source"]["source_sha256"]
        snapshot["cross_check_source"]["source_sha256"] = "b" * 64
        (cache / "raw" / ("b" * 64 + ".json")).write_bytes(
            (cache / "raw" / (original + ".json")).read_bytes()
        )
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    report = audit_package(package, market_cache=cache)
    assert report["status"] == "invalid"


def test_missing_backup_original_is_not_checked_while_primary_is_still_checked(tmp_path):
    package, cache = research_package(tmp_path)
    snapshot = package["research"]["evidence"][0]["snapshot"]
    (cache / "raw" / (snapshot["cross_check_source"]["source_sha256"] + ".json")).unlink()
    report = audit_package(package, market_cache=cache)
    assert report["status"] == "consistent", report["checks"]
    assert report["market_sources"]["checked"] == 1 and report["market_sources"]["missing"] == 1
    assert any(
        c["code"] == "raw_market_cross_check_missing" and c["status"] == "not_checked"
        for c in report["checks"]
    )
    assert any(c["code"] == "raw_market" and c["status"] == "pass" for c in report["checks"])

    changed = copy.deepcopy(package)
    changed["research"]["evidence"][0]["snapshot"]["points"][0]["close"] = "999"
    changed["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in changed.items() if k != "manifest"}
    )
    assert audit_package(changed, market_cache=cache)["status"] == "invalid"


def test_legacy_cross_check_without_reference_is_explicitly_not_checked(tmp_path):
    package, cache = research_package(tmp_path)
    package["research"]["evidence"][0]["snapshot"].pop("cross_check_source")
    package["manifest"]["payload_sha256"] = digest(
        {k: v for k, v in package.items() if k != "manifest"}
    )
    report = audit_package(package, market_cache=cache)
    assert report["status"] == "consistent", report["checks"]
    assert any(
        c["code"] == "raw_market_cross_check_missing" and c["status"] == "not_checked"
        for c in report["checks"]
    )
