import hashlib

from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.ifrs_sources import extract_ifrs_facts
from filingcopilot.models import Filing


def inline_report(cik, *, ads=False):
    eps_concept = (
        "ifrs-full:DilutedEarningsLossPerShareFromContinuingOperations"
        if not ads
        else "ifrs-full:DilutedEarningsLossPerShare"
    )
    ordinary_segment = (
        '<xbrli:segment><xbrldi:explicitmember dimension="ifrs-full:ClassesOfShareCapitalAxis">'
        "ifrs-full:OrdinarySharesMember</xbrldi:explicitmember></xbrli:segment>"
        if ads
        else ""
    )
    ads_context = (
        '<xbrli:context id="ads"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
        f"{cik}</xbrli:identifier><xbrli:segment><xbrldi:explicitmember dimension="
        '"ifrs-full:ClassesOfShareCapitalAxis">bp:AmericanDepositaryShareMember'
        "</xbrldi:explicitmember></xbrli:segment></xbrli:entity><xbrli:period>"
        "<xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>"
        "</xbrli:period></xbrli:context>"
        if ads
        else ""
    )
    return f"""
    <html><ix:header>
      <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
      <xbrli:context id="base"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="ordinary"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier>{ordinary_segment}</xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      {ads_context}
    </ix:header><body>
      <ix:nonfraction id="revenue" name="ifrs-full:Revenue" contextref="base" unitref="usd" decimals="0">100</ix:nonfraction>
      <ix:nonfraction id="eps" name="{eps_concept}" contextref="ordinary" unitref="usdPerShare" decimals="2">2</ix:nonfraction>
      {('<ix:nonfraction id="ads-eps" name="ifrs-full:DilutedEarningsLossPerShare" contextref="ads" unitref="usdPerShare" decimals="2">20</ix:nonfraction>' if ads else '')}
    </body></html>
    """.encode()


def filing(ticker, cik):
    raw = inline_report(cik, ads=ticker == "BP")
    return raw, Filing(
        id=ticker + ":fixture",
        ticker=ticker,
        company=DEFAULT_DOMAIN.companies[ticker].name,
        cik=cik,
        accession="0000000000-26-000000",
        form="20-F",
        filed="2026-03-01",
        period_end="2025-12-31",
        source_url="https://www.sec.gov/Archives/fixture.htm",
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def test_two_non_tsm_issuers_use_registered_20f_ifrs_profiles():
    assert DEFAULT_DOMAIN.annual_forms("BP") == ("20-F", "20-F/A")
    assert DEFAULT_DOMAIN.annual_forms("SHEL") == ("20-F", "20-F/A")
    for ticker, cik in [("BP", "0000313807"), ("SHEL", "0001306965")]:
        raw, report = filing(ticker, cik)
        facts = extract_ifrs_facts(raw, report, DEFAULT_DOMAIN)
        assert {fact.metric for fact in facts} >= {"revenue", "diluted_eps"}
        assert all(fact.unit.startswith("USD") for fact in facts)

    bp_facts = extract_ifrs_facts(*filing("BP", "0000313807"), DEFAULT_DOMAIN)
    assert {fact.unit for fact in bp_facts if fact.metric == "ads_diluted_eps"} == {"USD/ADS"}


def test_unregistered_issuer_has_no_foreign_numeric_profile():
    raw, report = filing("SHEL", "0001306965")
    report = report.model_copy(update={"ticker": "OTHER", "cik": "0001306965"})
    assert extract_ifrs_facts(raw, report, DEFAULT_DOMAIN) == []
