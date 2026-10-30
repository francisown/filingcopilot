"""Exercise the Agent -> real tool -> next model request boundary."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from filingcopilot.agent import ResearchAgent
from filingcopilot.audit import audit_package, create_package
from filingcopilot.market import MarketData
from filingcopilot.model_context import model_context_json
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.mark.parametrize("days", [2, 1500])
def test_calculation_tool_does_not_resend_full_price_history(tmp_path, days):
    now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    stamps = [int((now - timedelta(days=d)).timestamp()) for d in range(days, 0, -1)]
    price = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "AAPL",
                        "currency": "USD",
                        "instrumentType": "EQUITY",
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": 100,
                        "regularMarketTime": stamps[-1],
                    },
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{"close": [99] * len(stamps)}],
                        "adjclose": [{"adjclose": [99] * len(stamps)}],
                    },
                }
            ],
            "error": None,
        }
    }
    eps = {
        "timeseries": {
            "result": [
                {
                    "meta": {"symbol": ["AAPL"], "type": ["trailingDilutedEPS"]},
                    "trailingDilutedEPS": [
                        {
                            "asOfDate": "2026-06-30",
                            "periodType": "TTM",
                            "currencyCode": "USD",
                            "reportedValue": {"raw": 4},
                        }
                    ],
                }
            ],
            "error": None,
        }
    }
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off", min_model_interval=0))
    service.market = MarketData(
        tmp_path,
        now=lambda: now,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json=eps if "fundamentals-timeseries" in str(r.url) else price
                )
            )
        ),
    )
    tools = service.tools(now.date())
    valuation = tools.get_provider_ttm_valuation("AAPL")
    assert valuation["found"], valuation
    calculation = tools.ledger[valuation["evidence"]["id"]]
    calls = []

    class Gateway:
        def complete(self, messages, schemas, review=False):
            calls.append(json.loads(json.dumps(messages)))
            if len(calls) == 1:
                name = "calculate"
                arguments = {
                    "operation": "provider_ttm_pe",
                    "left_id": calculation["input_ids"][0],
                    "right_id": calculation["input_ids"][1],
                }
            else:
                name = "submit_answer"
                arguments = {
                    "claims": [{"kind": "calculation", "evidence_ids": [calculation["id"]]}]
                }
            return {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": str(len(calls)),
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
                "usage": {},
            }

    answer = ResearchAgent(service.settings, Gateway()).run(
        ResearchRequest(question="供应商TTM市盈率来源核对", ticker="AAPL", as_of=now.date()), tools
    )
    transmitted = next(m["content"] for m in calls[1] if m["role"] == "tool")
    assert '"points"' not in transmitted, f"Resent {len(transmitted):,} characters of history"
    assert len(transmitted) < 8000
    projected = json.loads(transmitted)
    assert projected["evidence"]["value"] == "25"
    assert projected["evidence"]["input_ids"] == calculation["input_ids"]
    market_input = projected["evidence"]["inputs"][0]
    assert market_input["statistics"]["trading_days"] == days
    assert market_input["statistics"]["quote_value"] == "100"
    assert market_input["snapshot"] == {
        k: v for k, v in calculation["inputs"][0]["snapshot"].items() if k != "points"
    }
    assert projected["evidence"]["inputs"][1] == calculation["inputs"][1]
    assert answer.status == "answered", (answer.gaps, answer.trace)
    frozen = next(e for e in answer.evidence if e["id"] == calculation["id"])
    assert len(frozen["inputs"][0]["snapshot"]["points"]) == days
    traced = next(s["result"]["evidence"] for s in answer.trace if s.get("tool") == "calculate")
    assert traced == frozen == tools.ledger[calculation["id"]]
    assert (
        audit_package(
            create_package(answer, service.domain, "AAPL"), market_cache=service.market.cache
        )["checks_failed"]
        == 0
    )


def test_context_preserves_original_text_dates_and_decimal_strings():
    context = {
        "question": "年内累计现金流与单季利润有什么区别？",
        "source_refresh": [{"checked": False, "warnings": ["请求超时，未完成更新检查。"]}],
        "evidence": [
            {
                "id": "source",
                "kind": "passage",
                "text": "  original\ntext: 1.230000 USD; points: [1, 2]  ",
                "filing": {"filed": "2026-07-31", "period_end": "2026-06-30"},
            },
            {"value": "1.230000", "start": "2026-01-01", "end": "2026-06-30"},
        ],
        "custom": {"points": [1, 2]},
    }
    assert json.loads(model_context_json(context)) == context


def test_context_compacts_statement_wrappers_but_keeps_rows_and_ids():
    payload = json.loads(
        model_context_json(
            {
                "statement": "income",
                "rows": [{"id": "row-1", "metric": "revenue", "value": "10"}],
                "evidence": [
                    {
                        "id": "row-1",
                        "kind": "fact",
                        "text": "large source wrapper",
                        "filing": {"id": "filing-1", "html": "x" * 5000},
                    }
                ],
            }
        )
    )
    assert payload["rows"][0]["id"] == "row-1"
    assert payload["evidence_ids"] == ["row-1"]
    assert "evidence" not in payload


def test_context_compacts_search_diagnostics_but_keeps_source_locator():
    payload = json.loads(
        model_context_json(
            {
                "query": "risk",
                "backend": "fts5+decomposed+title",
                "hits": [
                    {
                        "passage": {
                            "id": "p-1",
                            "filing_id": "AAPL:2025",
                            "ticker": "AAPL",
                            "text": "Supply disruptions could adversely affect operations.",
                            "section": "Risk Factors",
                            "source_url": "https://example.test/filing",
                            "locator": "paragraph 1",
                            "page": 12,
                        },
                        "score": 0.8,
                        "legs": {"lexical": {"rank": 1, "raw_score": -4}},
                        "anchor_coverage": 4,
                        "diversity": {"overlap_penalty": 0.1},
                    }
                ],
                "evidence_slot_plan": [{"id": "risk_disclosure", "required": True}],
                "continuations": [
                    {
                        "id": "p-2",
                        "filing_id": "AAPL:2025",
                        "ticker": "AAPL",
                        "text": "The disruption may also increase costs.",
                        "section": "Risk Factors",
                        "locator": "paragraph 2",
                    }
                ],
            }
        )
    )
    assert payload["hits"][0]["passage"]["id"] == "p-1"
    assert payload["hits"][0]["passage"]["page"] == 12
    assert "legs" not in payload["hits"][0]
    assert "anchor_coverage" not in payload["hits"][0]
    assert payload["evidence_slot_plan"][0]["id"] == "risk_disclosure"
    assert payload["continuations"][0]["id"] == "p-2"


def test_context_deduplicates_repeated_passage_text_across_rounds():
    hit = {
        "passage": {
            "id": "p-repeat",
            "filing_id": "AAPL:2025",
            "ticker": "AAPL",
            "text": "The same paragraph is returned by two related searches.",
            "section": "Risk Factors",
            "page": 9,
        },
        "score": 0.7,
    }
    seen = set()
    first = json.loads(model_context_json({"hits": [hit]}, seen_passage_ids=seen))
    second = json.loads(model_context_json({"hits": [hit]}, seen_passage_ids=seen))

    assert first["hits"][0]["passage"]["text"].startswith("The same")
    assert second["hits"][0]["passage"] == {
        "id": "p-repeat",
        "filing_id": "AAPL:2025",
        "ticker": "AAPL",
        "section": "Risk Factors",
        "page": 9,
        "reused": True,
    }
