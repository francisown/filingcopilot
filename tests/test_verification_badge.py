from xml.etree import ElementTree

import pytest

from filingcopilot.verification_badge import consistency_badge


@pytest.mark.parametrize('requested', [True, False])
def test_badge_exposes_checking_scope_without_semantic_claims(requested):
    svg = consistency_badge({'status': 'consistent', 'checks': [{'status': 'pass'}],
                             'raw_sources': {'requested': requested, 'filings_checked': 1,
                                             'facts_checked': 2, 'missing': 0}}, 'a'*64)
    ElementTree.fromstring(svg)
    assert 'a'*64 in svg
    assert 'Does not authenticate' in svg and 'certify semantic' in svg
    assert ('was not requested' in svg) == (not requested)
    assert 'href=' not in svg and '<script' not in svg


def test_failed_or_untrusted_badge_cannot_claim_passed():
    assert 'FAILED' in consistency_badge({'status': 'invalid', 'checks': [{'status': 'fail'}]}, 'b'*64)
    with pytest.raises(ValueError):
        consistency_badge({'status': 'consistent'}, '</text>')
