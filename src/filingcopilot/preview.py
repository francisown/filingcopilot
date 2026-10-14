"""Read-only, source-bound annual context delivered before model synthesis."""

import re
from datetime import date

from .models import Claim, Submission
from .quarterly import is_quarterly_question
from .tools import ResearchTools
from .ttm import is_ttm_question
from .verification import verify_submission


def cached_financial_preview(corpus, retriever, request):
    if is_quarterly_question(request.question) or is_ttm_question(request.question):
        return None  # Annual context must not masquerade as a requested quarter or TTM.
    domain = retriever.domain
    scopes = domain.company_scope(request.question)
    ticker = request.ticker or (next(iter(scopes)) if len(scopes) == 1 else None)
    if ticker not in domain.companies:
        return None
    if scopes and scopes != {ticker}:
        return None  # A selected company must not override a different question scope.
    years = {int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", request.question)}
    if len(years) > 1:
        return None
    filings = [f for f in corpus.catalog(request.as_of, ticker) if f['form'] in domain.annual_forms(ticker)]
    if not filings:
        return None
    year = next(iter(years)) if years else max(date.fromisoformat(f['period_end']).year for f in filings)
    # No acquisition, model, embedding or enrichment callback is installed here.
    tools = ResearchTools(corpus, retriever, request.as_of)
    requested, _ = domain.requests(request.question)
    metrics = sorted(requested) if requested else ['revenue', 'operating_income', 'net_income', 'operating_cash_flow']
    evidence_ids = []
    for metric in metrics[:6]:
        if metric not in domain.metrics:
            continue
        result = tools.get_financial_fact(ticker, metric, year)
        if result['found']:
            evidence_ids.append(result['evidence']['id'])
    if not evidence_ids:
        return None
    # This is explicitly annual context, separate from the eventual answer.
    # Validate the context itself with the same deterministic fact verifier.
    question = ' '.join([ticker, f'FY{year}', *[domain.metrics[tools.ledger[eid]['metric']].label for eid in evidence_ids]])
    claims, errors = verify_submission(Submission(claims=[Claim(kind='fact', evidence_ids=[eid]) for eid in evidence_ids]), tools, question, focus_ticker=ticker)
    if not claims:
        return None
    used = {eid for claim in claims for eid in claim['evidence_ids']}
    return {'schema': 'filingcopilot.financial-preview/v1', 'ticker': ticker,
            'as_of': request.as_of.isoformat(), 'fiscal_year': year,
            'claims': claims, 'evidence': [tools.ledger[eid] for eid in evidence_ids if eid in used],
            'gaps': errors,
            'freshness': 'cached_not_refreshed',
            'note': '已缓存年度数字，已通过数字与期间核验；尚未确认最新申报。它们是研究背景，模型解释和最终研究仍在进行。'}
