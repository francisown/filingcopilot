from decimal import Decimal

import pytest

from filingcopilot.cninfo import extract_cninfo_annual_metrics
from filingcopilot.hkex import extract_hkex_annual_metrics
from filingcopilot.localized_annual import extract_local_annual_metrics

HTML = """<table><caption>港元百万元 HKD million</caption>
<tr><th>项目</th><th>FY2024</th><th>FY2025</th></tr>
<tr><td>收入 Revenue</td><td>100</td><td>120</td></tr>
<tr><td>经营活动现金流 Operating cash flow</td><td>20</td><td>30</td></tr>
<tr><td>净利润 Net income</td><td>(5)</td><td>8</td></tr>
</table>""".encode()


def test_hkex_and_cninfo_annual_metrics_keep_header_unit_and_currency():
    rows = extract_local_annual_metrics(
        HTML,
        ticker="0700.HK",
        source_type="hkex",
        filing_id="hk-2025",
        source_url="https://www1.hkexnews.hk/listedco/listconews/a.htm",
    )
    assert {row.metric for row in rows} == {"revenue", "operating_cash_flow", "net_income"}
    revenue = next(row for row in rows if row.metric == "revenue")
    assert revenue.fiscal_year == 2025
    assert revenue.value == Decimal("120")
    assert revenue.currency == "HKD"
    assert revenue.language == "zh"
    assert "explicit FY2025" in revenue.locator
    assert extract_hkex_annual_metrics(
        HTML,
        ticker="0700.HK",
        filing_id="hk-2025",
        source_url="https://www1.hkexnews.hk/listedco/listconews/a.htm",
    )[0].source_type == "hkex"
    mainland = extract_local_annual_metrics(
        HTML.replace("港元百万元 HKD million".encode(), "人民币百万元 CNY million".encode()),
        ticker="600519.SS",
        source_type="cninfo",
        filing_id="cn-2025",
        source_url="https://static.cninfo.com.cn/finalpage/a.html",
    )
    assert all(row.source_type == "cninfo" and row.currency == "CNY" for row in mainland)
    assert extract_cninfo_annual_metrics(
        HTML.replace("港元百万元 HKD million".encode(), "人民币百万元 CNY million".encode()),
        ticker="600519.SS",
        filing_id="cn-2025",
        source_url="https://static.cninfo.com.cn/finalpage/a.html",
    )[0].source_type == "cninfo"


def test_localized_parser_rejects_missing_year_or_unit_instead_of_guessing():
    html = "<table><tr><th>项目</th><th>本期</th></tr><tr><td>收入</td><td>100</td></tr></table>".encode()
    assert not extract_local_annual_metrics(
        html,
        ticker="600519.SS",
        source_type="cninfo",
        filing_id="cn",
        source_url="https://static.cninfo.com.cn/finalpage/a.html",
    )
    with pytest.raises(ValueError):
        extract_local_annual_metrics(
            HTML,
            ticker="600519.SS",
            source_type="sec",
            filing_id="bad",
            source_url="https://example.com/a.html",
        )
