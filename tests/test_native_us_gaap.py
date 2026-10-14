import hashlib
import json
from datetime import date
from decimal import Decimal

import pytest

from filingcopilot.domain import DEFAULT_DOMAIN, Domain
from filingcopilot.ifrs_sources import extract_ifrs_facts
from filingcopilot.models import Filing

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_registered_chinese_issuers_bind_identity_taxonomy_and_native_currency():
    for ticker, cik in [('PDD', '0001737806'), ('BABA', '0001577552'), ('JD', '0001549802')]:
        profile = DEFAULT_DOMAIN.financial_profiles[ticker]
        assert profile.cik == cik and profile.taxonomy == 'us-gaap' and profile.currency == 'CNY'
        assert DEFAULT_DOMAIN.annual_forms(ticker) == ('20-F', '20-F/A')
        assert profile.metrics['diluted_eps'].unit == 'CNY/shares'
        assert profile.metrics['diluted_eps'].concepts == ['us-gaap:EarningsPerShareDiluted']
        assert 'ads_diluted_eps' not in profile.metrics
        assert all(metric.unit == 'CNY' for key, metric in profile.metrics.items() if key != 'diluted_eps')


def test_capabilities_expose_native_reporting_currency(tmp_path):
    from filingcopilot.service import FilingCopilot
    from filingcopilot.settings import Settings

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode='off'), gateway=False)
    for ticker in ('PDD', 'BABA', 'JD'):
        assert service.capabilities(ticker)['annual_reporting_currency'] == 'CNY'
        valuation = service.tools(date(2026, 10, 11)).get_stock_valuation(ticker)
        assert not valuation['found'] and 'CNY' in valuation['reason']
        assert ticker in valuation['reason'] and '新台币' not in valuation['reason']
        assert 'TSM' not in valuation['reason']
    assert service.capabilities('TSM')['annual_reporting_currency'] == 'TWD'


def native_source():
    from test_foreign_financials import annual_html
    html = annual_html().replace(b'0001046179', b'0001737806').replace(b'TWD', b'CNY')
    html = html.replace(b'ifrs-full:RevenueFromContractsWithCustomers', b'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax')
    html = html.replace(b'ifrs-full:GrossProfit', b'us-gaap:GrossProfit')
    html = html.replace(b'ifrs-full:ProfitLossAttributableToOwnersOfParent', b'us-gaap:NetIncomeLoss')
    html = html.replace(b'ifrs-full:Assets', b'us-gaap:Assets')
    return html


def test_native_us_gaap_rejects_translation_foreign_entity_segment_and_future():
    html = native_source()
    filing = Filing(id='PDD:fixture', ticker='PDD', company='PDD Holdings', cik='0001737806',
                    accession='0001737806-26-000001', form='20-F', filed=date(2026, 4, 29),
                    period_end=date(2025, 12, 31), source_url='https://www.sec.gov/Archives/fixture.htm',
                    content_sha256=hashlib.sha256(html).hexdigest())
    facts = extract_ifrs_facts(html, filing)
    revenue = [f for f in facts if f.metric == 'revenue' and f.fiscal_year == 2025]
    assert revenue and {f.value for f in revenue} == {Decimal('1200000000')}
    assert all(f.unit == 'CNY' and f.locator.startswith('inline-us-gaap:') for f in facts)
    assert all(f.fiscal_year <= 2025 for f in facts)
    assert not extract_ifrs_facts(html, filing.model_copy(update={'cik': '0000000001'}))
    assert not any(f.metric.endswith('eps') for f in facts)


def test_registry_rejects_mixed_taxonomies_or_convenience_usd():
    raw = DEFAULT_DOMAIN.model_dump()
    raw['financial_profiles']['PDD']['metrics']['revenue']['unit'] = 'USD'
    with pytest.raises(ValueError, match='native'):
        Domain.model_validate(raw)


@pytest.mark.parametrize('tamper', [False, True])
def test_new_ordinary_eps_backfills_only_hash_matched_original_without_network(tmp_path, monkeypatch, tamper):
    from filingcopilot.models import ResearchRequest
    from filingcopilot.service import FilingCopilot
    from filingcopilot.settings import Settings

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode='off'), gateway=False)
    html = native_source().replace(b'ifrs-full:DilutedEarningsLossPerShare', b'us-gaap:EarningsPerShareDiluted')
    filing = Filing(id='PDD:eps', ticker='PDD', company='PDD Holdings', cik='0001737806',
                    accession='0001737806-26-000001', form='20-F', filed='2026-04-29',
                    period_end='2025-12-31', source_url='https://www.sec.gov/Archives/eps.htm',
                    content_sha256=hashlib.sha256(html).hexdigest())
    old_facts = [f for f in extract_ifrs_facts(html,filing) if f.metric != 'diluted_eps']
    service.corpus.ingest(filing,old_facts,[])
    before = {f.id:f.model_dump_json() for f in old_facts}
    path = tmp_path/'sec-cache'/(hashlib.sha256(filing.source_url.encode()).hexdigest()+'.cache')
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(html+b'altered' if tamper else html)
    monkeypatch.setattr(service.discovery.fetcher, 'fetch', lambda *a, **k: pytest.fail('Offline backfill must not request network'))
    request = ResearchRequest(question='PDD FY2025 稀释每股收益',ticker='PDD',as_of='2026-10-11',mode='reference')
    if tamper:
        with pytest.raises(ValueError,match='Cached filing bytes differ'):
            service.research(request)
        assert not service.corpus.facts('PDD','diluted_eps',2025,date(2026,10,11),('20-F','20-F/A'))
        return
    result = service.research(request)
    eps = [e for e in result.evidence if e.get('metric')=='diluted_eps']
    assert eps and eps[0]['unit']=='CNY/shares' and Decimal(eps[0]['value'])==Decimal('2.40')
    service.enrich_cached_facts('PDD')
    assert len(service.corpus.facts('PDD','diluted_eps',2025,date(2026,10,11),('20-F','20-F/A')))==1
    for eid, payload in before.items():
        item = service.corpus.evidence(eid,date(2026,10,11))
        assert item['value']==json.loads(payload)['value']


def test_jd_consolidated_profit_does_not_take_parent_only_net_income():
    html = native_source().replace(b'0001737806', b'0001549802')
    html = html.replace(b'us-gaap:NetIncomeLoss', b'us-gaap:ProfitLoss')
    filing = Filing(id='JD:fixture', ticker='JD', company='JD.com', cik='0001549802',
                    accession='0001549802-26-000001', form='20-F', filed=date(2026, 4, 16),
                    period_end=date(2025, 12, 31), source_url='https://www.sec.gov/Archives/fixture.htm',
                    content_sha256=hashlib.sha256(html).hexdigest())
    facts = extract_ifrs_facts(html, filing)
    assert any(f.metric == 'net_income' and f.concept == 'us-gaap:ProfitLoss' for f in facts)


def test_native_us_gaap_package_replays_bound_html_once(tmp_path):
    from filingcopilot.audit import audit_package
    from filingcopilot.models import ResearchRequest
    from filingcopilot.service import FilingCopilot
    from filingcopilot.settings import Settings

    service = FilingCopilot(Settings(home=tmp_path, embedding_mode='off'), gateway=False)
    html = native_source()
    filing = Filing(id='PDD:fixture', ticker='PDD', company='PDD Holdings', cik='0001737806',
                    accession='0001737806-26-000001', form='20-F', filed=date(2026, 4, 29),
                    period_end=date(2025, 12, 31), source_url='https://www.sec.gov/Archives/fixture.htm',
                    content_sha256=hashlib.sha256(html).hexdigest())
    facts = extract_ifrs_facts(html, filing)
    service.corpus.ingest(filing, facts, [])
    cache = tmp_path / 'sec-cache'
    cache.mkdir(exist_ok=True)
    (cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + '.cache')).write_bytes(html)
    answer = service.research(ResearchRequest(question='PDD FY2025 营收', ticker='PDD',
                                             as_of='2026-10-11', mode='reference'))
    assert answer.answer
    report = audit_package(service.export_package(answer.id), source_cache=cache)
    assert report['status'] == 'consistent', report['checks']
    assert report['raw_sources']['facts_checked'] == 1
    raw = DEFAULT_DOMAIN.model_dump()
    raw['financial_profiles']['PDD']['metrics']['revenue']['concepts'] = ['ifrs-full:Revenue']
    with pytest.raises(ValueError, match='native'):
        Domain.model_validate(raw)
