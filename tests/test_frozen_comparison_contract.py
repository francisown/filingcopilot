from dataclasses import replace
from pathlib import Path

import pytest

from filingcopilot.corpus import Corpus
from filingcopilot.settings import Settings
from scripts.compare_frozen_agents import (
    ensure_case_sources,
    frozen_budgets,
    validate_budget_models,
)


@pytest.mark.parametrize('change', [
    {'model': 'different'}, {'review_model': 'different'}, {'research_timeout': 30},
    {'min_model_interval': 0}, {'review_max_tokens': 20}, {'max_rounds': 1},
])
def test_resume_rejects_different_model_or_budget_without_calls(change):
    settings = Settings(home=Path('unused-test-only'), model='frozen', review_model='frozen-review')
    manifest = {'model': settings.model, 'review_model': settings.review_model,
                'budgets': frozen_budgets(settings)}
    validate_budget_models(manifest, settings)
    with pytest.raises(ValueError, match='Resume cannot change'):
        validate_budget_models(manifest, replace(settings, **change))


@pytest.mark.slow
def test_missing_case_is_recreated_but_changed_sources_are_not_overwritten(tmp_path):
    filing = {'id':'PDD:fixture','ticker':'PDD','company':'Public fixture','cik':'0001737806',
              'accession':'fixture','form':'20-F','filed':'2026-04-29','period_end':'2025-12-31',
              'source_url':'https://www.sec.gov/Archives/fixture.htm','content_sha256':'a'*64}
    passage = {'id':'p1','filing_id':filing['id'],'ticker':'PDD','section':'Business',
               'text':'Frozen public material','source_url':filing['source_url'],
               'locator':'paragraph 1','table_context':None}
    bundle = {'id':'case','filing':filing,'facts':[],'passages':[passage]}
    with pytest.raises(ValueError, match='inside the comparison home'):
        ensure_case_sources(tmp_path, {**bundle, 'id':'../../outside'})
    ensure_case_sources(tmp_path, bundle)
    ensure_case_sources(tmp_path, bundle)
    corpus = Corpus(tmp_path/'case')
    with corpus.connect() as db:
        db.execute('UPDATE passages SET payload=?', ('{}',))
    with pytest.raises((KeyError, ValueError)):
        ensure_case_sources(tmp_path, bundle)
    with corpus.connect() as db:
        assert db.execute('SELECT payload FROM passages').fetchone()[0] == '{}'
