import hashlib
import json

import pytest

from scripts.build_blind_review_desk import build
from scripts.score_semantic_blind_labels import read_labels, score


def test_offline_page_preserves_sample_and_does_not_leak_decisions(tmp_path):
    sample, page = tmp_path/'blind.json', tmp_path/'desk.html'
    rows = [{'blind_id': 'one', 'answer': '</script><script>bad()</script>',
             'label': None, 'review_approved': True, 'private_decision': 'hidden', 'evidence': []}]
    sample.write_text(json.dumps(rows), encoding='utf-8')
    before = sample.read_bytes()
    result = build(sample, page)
    html = page.read_text(encoding='utf-8')
    assert 'private_decision' not in html and 'review_approved' not in html
    assert '\\u003c/script>' in html
    assert 'connect-src' not in html and "default-src 'none'" in html
    assert result['labels_filled'] == 0
    assert result['sample_sha256'] == hashlib.sha256(before).hexdigest()
    assert sample.read_bytes() == before


def test_export_identity_and_incomplete_labels_remain_explicit():
    payload = {'schema': 'filingcopilot.human-label-export/v1', 'human_reviewer': 'fixture-only',
               'human_attestation': 'self_declared', 'rows': [{'blind_id': 'a', 'label': None}]}
    rows, reviewer = read_labels(payload)
    assert score(rows, reviewer=reviewer)['status'] == 'awaiting_human_labels'
    with pytest.raises(ValueError, match='conflicts'):
        read_labels(payload, 'different')
    payload['human_reviewer'] = ''
    with pytest.raises(ValueError, match='declaration'):
        read_labels(payload)


def test_legacy_rows_still_need_a_real_explicit_reviewer():
    rows, reviewer = read_labels([{'blind_id': 'a', 'label': 'support'}])
    assert reviewer is None
    assert score(rows, reviewer=reviewer)['human_acceptance_status'] == 'awaiting_human_reviewer'
