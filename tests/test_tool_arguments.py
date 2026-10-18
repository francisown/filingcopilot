"""Provider-encoded scalar arguments must preserve the requested search scope."""

from datetime import date

import pytest

from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

pytestmark = pytest.mark.slow


@pytest.mark.parametrize(
    "encoded",
    [{"k": "6"}, {"fiscal_year": "2025"}, {"k": "6", "fiscal_year": "2025"}],
)
def test_string_integer_search_arguments_match_typed_query(tmp_path, encoded):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    arguments = {
        "ticker": "NVDA",
        "fiscal_year": 2025,
        "query": "Gaming revenue Data Center demand",
        "k": 6,
    }
    expected = service.tools(date(2026, 10, 8)).call("search_passages", arguments)
    assert expected["hits"]
    actual = service.tools(date(2026, 10, 8)).call("search_passages", {**arguments, **encoded})
    assert actual == expected


@pytest.mark.parametrize("value", ["6.5", "6 items", "", "null", True, 6.5, [], {}])
def test_ambiguous_integer_arguments_are_rejected(tmp_path, value):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    with pytest.raises(ValueError, match=r"search_passages\.k must be an integer"):
        service.tools(date(2026, 10, 8)).call("search_passages", {"query": "revenue", "k": value})


@pytest.mark.parametrize("value, expected", [("false", False), ("true", True), (False, False)])
def test_boolean_strings_keep_false_false(tmp_path, monkeypatch, value, expected):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    tools = service.tools(date(2026, 10, 8))
    monkeypatch.setattr(tools, "get_provider_ttm_valuation", lambda **kwargs: kwargs)
    original = {"ticker": "AAPL", "include_pe": value}
    result = tools.call("get_provider_ttm_valuation", original)
    assert result["include_pe"] is expected
    assert original["include_pe"] == value


@pytest.mark.parametrize("value", ["yes", "0", 0, 1])
def test_ambiguous_boolean_arguments_are_rejected(tmp_path, value):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    with pytest.raises(ValueError, match=r"include_pe must be a boolean"):
        service.tools(date(2026, 10, 8)).call("get_provider_ttm_valuation", {"ticker": "AAPL", "include_pe": value})


def test_optional_year_null_and_text_are_not_reinterpreted(tmp_path, monkeypatch):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    tools = service.tools(date(2026, 10, 8))
    monkeypatch.setattr(tools, "search_passages", lambda **kwargs: kwargs)
    arguments = {"query": "2025", "fiscal_year": None, "k": "100"}
    assert tools.call("search_passages", arguments) == {"query": "2025", "fiscal_year": None, "k": 100}
    assert arguments["k"] == "100"
