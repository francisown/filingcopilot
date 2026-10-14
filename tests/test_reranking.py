import copy
from datetime import date

import pytest

from filingcopilot.models import Filing
from filingcopilot.reranking import rerank_candidates, scoped_candidate_hits


class Scorer:
    def __init__(self, result):
        self.result = result

    def rerank(self, query, documents):
        assert query == "revenue"
        assert documents == ["source A", "source B"]
        return self.result


def hits():
    return [
        {"passage": {"id": id_, "text": text, "ticker": "AAPL"}, "score": 0.1}
        for id_, text in [("a", "source A"), ("b", "source B")]
    ]


def test_reordering_preserves_original_evidence_and_input():
    original = hits()
    before = copy.deepcopy(original)
    result = rerank_candidates("revenue", original, Scorer([0.1, 0.9]))
    assert result["applied"] and result["hits"][0]["passage"] == before[1]["passage"]
    assert original == before


@pytest.mark.parametrize("scores", [[], [1], [1, float("nan")], [float("inf"), 0]])
def test_invalid_scores_restore_exact_original_order(scores):
    original = hits()
    result = rerank_candidates("revenue", original, Scorer(scores))
    assert not result["applied"] and result["error"] == "ValueError"
    assert result["hits"] == original


def test_ties_are_reproducible_and_empty_candidates_do_not_load_model():
    assert [
        h["passage"]["id"]
        for h in rerank_candidates(
            "revenue", list(reversed(hits())), type("Tie", (), {"rerank": lambda *a: [1, 1]})()
        )["hits"]
    ] == ["a", "b"]
    assert rerank_candidates("revenue", [], None) == {"hits": [], "applied": False, "error": None}


def test_full_pool_recovers_target_outside_public_eight_without_gold(tools):
    original = tools.corpus.passages(date(2026, 10, 8), 'AAPL')[0]
    rows = [original.model_copy(update={'id': f'candidate-{i}'}) for i in range(12)]
    filing = tools.corpus.catalog(date(2026, 10, 8), 'AAPL')[0]
    tools.corpus.ingest(Filing.model_validate(filing), [], rows)
    result = {'hits': [{'passage': p.model_dump(mode='json'), 'score': .1} for p in rows[:8]],
              'retrieval_diagnostics': {'candidate_ids': [p.id for p in rows]}}
    pool = scoped_candidate_hits(tools.corpus, result, date(2026, 10, 8), 'AAPL', 2025)
    assert len(pool) == 12
    trial = rerank_candidates('query', pool, type('Scorer', (), {'rerank': lambda *args: list(range(12))})())
    assert trial['hits'][0]['passage']['id'] == 'candidate-11'
    assert len(result['hits']) == 8
    result['retrieval_diagnostics']['candidate_ids'].append('untrusted-outside-scope')
    with pytest.raises(ValueError, match='outside'):
        scoped_candidate_hits(tools.corpus, result, date(2026, 10, 8), 'AAPL', 2025)
