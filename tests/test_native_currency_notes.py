from filingcopilot.cashflow import cashflow_explanation, native_definition_note
from filingcopilot.domain import DEFAULT_DOMAIN


def test_native_cashflow_note_matches_profile_currency_and_standard():
    for ticker in ('PDD','BABA','JD'):
        note=native_definition_note(DEFAULT_DOMAIN.financial_profiles[ticker])
        assert 'CNY' in note and 'US-GAAP' in note and '新台币' not in note and 'IASB' not in note
    assert '新台币' in native_definition_note(DEFAULT_DOMAIN.financial_profiles['TSM'])


def test_cashflow_purpose_and_quality_are_explanations_while_amount_is_direct():
    assert cashflow_explanation('自由现金流主要用于什么？')
    assert cashflow_explanation('How is free cash flow used?')
    assert not cashflow_explanation('How much free cash flow?')
