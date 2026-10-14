"""Opt-in localhost acceptance against real provider data; never run in offline CI."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from filingcopilot.audit import audit_package
from filingcopilot.comparison import validate_comparison
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8135")
    parser.add_argument("--output", type=Path, default=Path("artifacts/comparison-real-0.34.json"))
    args = parser.parse_args()
    service = FilingCopilot(Settings.from_env())
    records = []
    cases = [
        ("four_companies", ["AAPL", "MSFT", "NVDA", "AMZN"], "5Y", date.today(), True),
        ("one_year", ["MSFT", "AMZN"], "1Y", date.today(), False),
        ("late_ipo", ["ARM", "AMZN"], "MAX", date.today(), False),
        ("different_currency", ["0700.HK", "AMZN"], "5Y", date.today(), False),
        ("historical", ["MSFT", "AMZN"], "5Y", date(2026, 2, 1), False),
    ]
    for name, tickers, period, day, refresh in cases:
        with httpx.Client(timeout=120, trust_env=False) as client:
            response = client.post(
                args.url + "/api/comparison",
                json={
                    "tickers": tickers,
                    "period": period,
                    "as_of": day.isoformat(),
                    "refresh_sources": refresh,
                },
            )
            response.raise_for_status()
            answer = response.json()
            package = client.get(args.url + f"/api/runs/{answer['id']}/package").json()
        result = answer["trace"][0]["result"]
        ledger = {e["id"]: e for e in answer["evidence"]}
        validate_comparison(result, ledger, service.refresh_domain(), day)
        report = audit_package(
            package,
            corpus=service.corpus,
            trusted_domain=service.domain,
            source_cache=service.settings.home / "sec-cache",
            market_cache=service.market.cache,
        )
        assert report["status"] == "consistent", report
        assert result["chart"]["found"] and answer["usage"]["model_calls"] == 0
        if name == "different_currency":
            assert len({s["currency"] for s in result["chart"]["series"]}) == 2
            assert not result["rows"][0]["annual_basis_id"]
        if name == "late_ipo":
            assert result["chart"]["start"] >= max(
                s["observed_start"] for s in result["chart"]["series"]
            )
        if name == "historical":
            assert all("annual_pe" not in r["cells"] for r in result["rows"])
            assert result["chart"]["end"] <= day.isoformat()
        raw = json.dumps(package, ensure_ascii=False, indent=2).encode()
        path = args.output.parent / f"comparison-{name}-0.34-package.json"
        path.write_bytes(raw)
        path.with_name(path.name.replace("-package", "-audit")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        records.append(
            {
                "case": name,
                "run_id": answer["id"],
                "status": answer["status"],
                "period": period,
                "as_of": day.isoformat(),
                "elapsed_ms": answer["elapsed_ms"],
                "model_calls": 0,
                "chart_start": result["chart"]["start"],
                "chart_end": result["chart"]["end"],
                "common_days": result["chart"]["common_days"],
                "currencies": [s["currency"] for s in result["chart"]["series"]],
                "rows": [
                    {"ticker": r["ticker"], "metrics": list(r["cells"]), "issues": r["issues"]}
                    for r in result["rows"]
                ],
                "audit": report["status"],
                "source_validation": report["source_validation"],
                "raw_source_missing": report["raw_sources"]["missing"],
                "market_source_missing": report["market_sources"]["missing"],
                "package_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    args.output.write_text(
        json.dumps(
            {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "cases": records,
                "scope": "Real source/period/currency/precision/replay acceptance; not model or investment judgement accuracy.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
