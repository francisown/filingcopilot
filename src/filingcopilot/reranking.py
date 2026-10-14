"""Evaluation-only candidate reranking; production retrieval stays calibrated.

A scorer may reorder the source-scoped candidate set, never add new evidence.
Failure leaves the exact original order intact and records the failure type.
"""

from __future__ import annotations

import copy
import math
from typing import Protocol


class CandidateScorer(Protocol):
    def rerank(self, query: str, documents: list[str]): ...


def scoped_candidate_hits(corpus, result, as_of, ticker, fiscal_year):
    """Recover the complete frozen retrieval pool for evaluation, without gold.

    Public search still returns at most eight hits. A reordering experiment
    must explicitly use the diagnosed pool or it cannot test ranking misses
    outside those eight. Resolve IDs only inside the same trusted source scope.
    """
    ids = result['retrieval_diagnostics']['candidate_ids']
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate candidate IDs')
    scoped = {p.id: p for p in corpus.passages(as_of, ticker, fiscal_year)}
    if any(eid not in scoped for eid in ids):
        raise ValueError('Candidate outside frozen company/date/year scope')
    known = {hit['passage']['id']: hit for hit in result['hits']}
    return [copy.deepcopy(known[eid]) if eid in known else
            {'passage': scoped[eid].model_dump(mode='json'), 'score': 0.0}
            for eid in ids]


def rerank_candidates(query: str, hits: list[dict], scorer: CandidateScorer) -> dict:
    original = copy.deepcopy(hits)
    if not hits:
        return {"hits": original, "applied": False, "error": None}
    try:
        ids = [hit["passage"]["id"] for hit in hits]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate candidate evidence IDs")
        scores = list(scorer.rerank(query, [hit["passage"]["text"] for hit in hits]))
        if len(scores) != len(hits) or any(not math.isfinite(float(v)) for v in scores):
            raise ValueError("Invalid reranker scores")
        for hit, score in zip(original, scores, strict=True):
            hit["rerank_score"] = float(score)
        original.sort(key=lambda hit: (-hit["rerank_score"], hit["passage"]["id"]))
        return {"hits": original, "applied": True, "error": None}
    except Exception as exc:
        return {"hits": copy.deepcopy(hits), "applied": False, "error": type(exc).__name__}
