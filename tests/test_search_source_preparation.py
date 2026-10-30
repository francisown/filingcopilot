from datetime import date


def test_search_prepares_missing_year_once(tools):
    calls = []

    def loader(ticker, as_of, *, fiscal_years=None, quarterly=False):
        calls.append((ticker, as_of, fiscal_years, quarterly))
        return {"checked": True, "acquired": [{"filing": "AAPL-2026"}]}

    tools.filing_loader = loader
    first = tools.search_passages(
        "supply chain FY2026", "AAPL", fiscal_year=2026, lexical_only=True
    )
    second = tools.search_passages(
        "supply chain FY2026", "AAPL", fiscal_year=2026, lexical_only=True
    )

    assert calls == [("AAPL", date(2026, 10, 8), [2026], False)]
    assert first["source_preparation"]["attempted"] is True
    assert first["source_preparation"]["acquired_count"] == 1
    assert second["source_preparation"] == first["source_preparation"]


def test_search_does_not_refresh_available_scope(tools):
    calls = []
    tools.filing_loader = lambda *args, **kwargs: calls.append((args, kwargs))

    result = tools.search_passages("supply", "AAPL", fiscal_year=2025, lexical_only=True)

    assert result["hits"]
    assert calls == []
