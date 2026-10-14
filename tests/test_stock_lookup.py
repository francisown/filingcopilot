import httpx
import pytest
from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.market import MarketData, MarketError
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.mark.parametrize("query", ["台积电", "台積電", "TSMC", "tsmc"])
def test_tsmc_lookup_preserves_primary_listings_without_llm_or_quote(tmp_path, query):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))

    def provider(request):
        # A name search that returns only peripheral listings must not hide TSM.
        if "/search" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "quotes": [
                        {
                            "symbol": "TSMC34.SA",
                            "longname": "Taiwan Semiconductor",
                            "exchange": "SAO",
                            "quoteType": "EQUITY",
                        }
                    ]
                },
            )
        return httpx.Response(503)

    service.market.client = httpx.Client(transport=httpx.MockTransport(provider))
    response = TestClient(create_app(service)).post(
        "/api/stocks/search", json={"query": query, "translate": False}
    )
    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert [row["ticker"] for row in candidates][:2] == ["TSM", "2330.TW"]
    assert candidates[0]["currency"] == "USD"
    assert candidates[1]["currency"] == "TWD"
    assert candidates[0]["filings_supported"] and candidates[0]["annual_financials"]
    assert all(not row["filings_supported"] for row in candidates[1:])
    assert "20-F" in candidates[0]["filing_note"]
    assert service.capabilities("TSM")["annual_only"] is True
    assert service.capabilities("TSM")["annual_text_only"] is False
    assert len(service.capabilities("TSM")["annual_financial_metrics"]) == 11
    assert service.capabilities("TSM")["annual_ads_valuation"]
    assert service.capabilities("TSM")["annual_cashflow"]
    assert service.capabilities("TSM")["financial_trend"]["enabled"]
    assert "过去五年营收、利润和现金流趋势怎么样？" in service.capabilities("TSM")["questions"]
    assert len(service.capabilities("2330.TW")["questions"]) == 3
    assert any("SPY" in q for q in service.capabilities("2330.TW")["questions"])


def test_explicit_brazilian_listing_is_not_substituted_with_us_adr(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.market.client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    result = service.find_stock("TSMC34.SA", False)
    assert [row["ticker"] for row in result["candidates"]] == ["TSMC34.SA"]
    assert result["candidates"][0]["currency"] == "BRL"


def test_unavailable_market_names_the_security_and_needs_no_filing(tmp_path):
    calls = []

    def unavailable(request):
        calls.append(request.url.host)
        return httpx.Response(503)

    market = MarketData(tmp_path, client=httpx.Client(transport=httpx.MockTransport(unavailable)))
    with pytest.raises(
        MarketError, match="TSMC34.SA.*行情源暂未返回可用数据.*无需先下载财报"
    ) as error:
        market.history("TSMC34.SA")
    assert error.value.status["cause"] == "upstream"
    assert calls == ["query1.finance.yahoo.com", "query2.finance.yahoo.com", "api.nasdaq.com"]


def test_custom_listing_directory_is_shared_by_lookup_and_capabilities(tmp_path):
    import json

    config = tmp_path / "market.json"
    config.write_text(
        json.dumps(
            {
                "example": {
                    "aliases": ["示例公司"],
                    "listings": [
                        {
                            "ticker": "EXAMPLE.TW",
                            "name": "Example",
                            "exchange": "台湾证券交易所",
                            "currency": "TWD",
                            "product": "普通股",
                            "filings_supported": False,
                            "filing_note": "尚未接入",
                            "source_url": "https://example.com/investor",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    service = FilingCopilot(
        Settings(home=tmp_path / "home", embedding_mode="off", market_config=config)
    )
    assert service.find_stock("示例公司", False)["candidates"][0]["ticker"] == "EXAMPLE.TW"
    assert len(service.capabilities("EXAMPLE.TW")["questions"]) == 3
    assert any("SPY" in q for q in service.capabilities("EXAMPLE.TW")["questions"])
