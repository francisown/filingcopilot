import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from filingcopilot.market import MarketData, SourceFailure
from filingcopilot.market_nasdaq import nasdaq_url, parse_nasdaq

URL = (
    "https://api.nasdaq.com/api/quote/LITE/historical?assetclass=stocks&"
    "fromdate=2026-01-01&todate=2026-10-13&limit=5000&offset=0"
)
NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
BODY = {
    "data": {
        "symbol": "LITE",
        "totalRecords": 2,
        "tradesTable": {
            "rows": [
                {"date": "10/12/2026", "close": "$20.50", "volume": "1,200"},
                {"date": "10/09/2026", "close": "$19.25", "volume": "900"},
            ]
        },
    }
}


def test_nasdaq_url_and_parser_are_source_bound():
    assert nasdaq_url(URL)
    snapshot = parse_nasdaq(
        json.dumps(BODY).encode(), "LITE", "1M", date(2026, 10, 13), NOW, URL
    )
    assert snapshot.source_name == "Nasdaq"
    assert snapshot.basis == "close"
    assert snapshot.points[-1].date == date(2026, 10, 12)
    assert snapshot.quote_value == 20.50
    assert snapshot.points[-1].volume == 1200


def test_nasdaq_fallback_is_used_after_primary_failure_and_cached(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json=BODY, request=request)

    market = MarketData(
        Path(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )
    market.yahoo_source.fetch_daily = lambda *args, **kwargs: SourceFailure(
        "Yahoo Finance", "LITE", "connection", "controlled", ConnectionError("controlled")
    )
    first = market.history("LITE", "1M", date(2026, 10, 13))
    second = market.history("LITE", "1M", date(2026, 10, 13))
    assert first.source_name == second.source_name == "Nasdaq"
    assert len(calls) == 1

