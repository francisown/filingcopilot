import hashlib
from datetime import date

import pytest

from filingcopilot.models import Filing
from scripts.inspect_ads_inputs import inspect_source


def source(*, numerator="us-gaap:NetIncomeLossAvailableToCommonStockholdersDiluted",
           decimals="INF", value="90", shares="20", extra="", period_start="2025-01-01"):
    return f'''<html><xbrli:context id="annual"><xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001577552</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:startdate>2025-01-01</xbrli:startdate>
    <xbrli:enddate>2025-12-31</xbrli:enddate></xbrli:period></xbrli:context>
    <xbrli:context id="shares-period"><xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001577552</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:startdate>{period_start}</xbrli:startdate>
    <xbrli:enddate>2025-12-31</xbrli:enddate></xbrli:period></xbrli:context>
    <xbrli:unit id="cny"><xbrli:measure>iso4217:CNY</xbrli:measure></xbrli:unit>
    <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
    <ix:nonfraction id="income" name="{numerator}" contextref="annual" unitref="cny"
      decimals="{decimals}" scale="0">{value}</ix:nonfraction>
    <ix:nonfraction id="denom" name="us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
      contextref="shares-period" unitref="shares" decimals="{decimals}">{shares}</ix:nonfraction>
    {extra}</html>'''.encode()


def filing(raw):
    return Filing(id="BABA:synthetic-ads", ticker="BABA", company="Synthetic issuer",
                  cik="0001577552", accession="synthetic-ads", form="20-F", filed="2026-04-01",
                  period_end="2025-12-31", source_url="https://www.sec.gov/Archives/synthetic.htm",
                  content_sha256=hashlib.sha256(raw).hexdigest())


def inspect(raw):
    return inspect_source(raw, filing(raw), date(2026, 10, 12))


@pytest.mark.parametrize("decimals", ["2", "-3", "-6"])
def test_finite_underlying_precision_does_not_unlock_unrounded_conversion(decimals):
    result = inspect(source(decimals=decimals))
    assert result["candidates"] and not any(result["unrounded_component_candidates"].values())
    assert {"diluted_numerator_unrounded", "diluted_denominator_unrounded"} <= set(result["missing"])


@pytest.mark.parametrize("concept", ["us-gaap:NetIncomeLoss", "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic"])
def test_basic_or_consolidated_profit_cannot_be_promoted_to_diluted_numerator(concept):
    result = inspect(source(numerator=concept))
    assert result["unrounded_component_candidates"]["diluted_denominator"]
    assert not result["unrounded_component_candidates"]["diluted_numerator"]


@pytest.mark.parametrize("change", ["currency", "entity", "dimension", "duplicate", "nil"])
def test_source_boundaries_and_ambiguous_contexts_withhold_candidates(change):
    raw = source()
    if change == "currency":
        raw = raw.replace(b"iso4217:CNY", b"iso4217:USD")
    elif change == "entity":
        raw = raw.replace(b"0001577552", b"0001737806")
    elif change == "dimension":
        raw = raw.replace(b"</xbrli:entity>", b'<xbrli:segment><xbrldi:explicitmember dimension="a">segment</xbrldi:explicitmember></xbrli:segment></xbrli:entity>')
    elif change == "duplicate":
        raw = raw.replace(b"</html>", b'<xbrli:context id="annual"></xbrli:context></html>')
    else:
        raw = raw.replace(b'id="income"', b'id="income" xsi:nil="true"')
    result = inspect(raw)
    assert not result["unrounded_component_candidates"]["diluted_numerator"]
    assert result["rejected"]


def test_exact_components_still_need_effective_ratio_fx_and_matching_windows():
    result = inspect(source())
    assert result["unrounded_component_periods_align"]
    assert set(result["missing"]) == {"source_bound_effective_ADS_ratio", "dated_sourced_FX_snapshot_with_direction"}
    assert not result["ready"] and result["ads_eps"] is None and result["ads_pe"] is None
    mismatch = inspect(source(period_start="2025-01-02"))
    assert all(mismatch["unrounded_component_candidates"].values())
    assert not mismatch["unrounded_component_periods_align"]
    assert "aligned_native_annual_component_periods" in mismatch["missing"]


def test_conflicting_diluted_inputs_withhold_exact_candidates():
    duplicate = '''<ix:nonfraction id="income-other"
      name="us-gaap:NetIncomeLossAvailableToCommonStockholdersDiluted"
      contextref="annual" unitref="cny" decimals="INF">91</ix:nonfraction>'''
    result = inspect(source(extra=duplicate))
    assert not result["unrounded_component_candidates"]["diluted_numerator"]
    same_value = inspect(source(extra=duplicate.replace(">91<", ">90.00<")))
    assert same_value["unrounded_component_candidates"]["diluted_numerator"]


def test_declared_exact_input_keeps_all_digits_and_loss_sign():
    digits = "12345678901234567890123456789012345678901234567890123456789"
    raw = source(value=digits).replace(b'decimals="INF" scale="0"', b'decimals="INF" scale="3" sign="-"')
    result = inspect(raw)
    numerator = next(r for r in result["candidates"] if r["role"] == "diluted_numerator")
    assert numerator["reported_value"] == "-" + digits + "000"
    assert numerator["raw_text"] == digits and numerator["sign"] == "-"
    assert not result["ready"] and result["ads_eps"] is None


@pytest.mark.parametrize("shares", ["0", "No", "NaN"])
def test_missing_or_nonpositive_denominator_cannot_become_exact_input(shares):
    result = inspect(source(shares=shares))
    assert not result["unrounded_component_candidates"]["diluted_denominator"]


def test_hash_and_public_date_are_required_and_input_is_read_only():
    raw = source()
    before = raw
    with pytest.raises(ValueError, match="SHA256"):
        inspect_source(raw + b"altered", filing(raw), date(2026, 10, 12))
    with pytest.raises(ValueError, match="not public"):
        inspect_source(raw, filing(raw), date(2026, 3, 31))
    inspect(raw)
    assert raw == before
