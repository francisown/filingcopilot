import threading
from datetime import date
from pathlib import Path

import pytest

from filingcopilot.execution import progress, research_scope
from filingcopilot.jobs import ResearchJobs
from filingcopilot.models import ResearchRequest
from filingcopilot.preview import cached_financial_preview
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_job_exposes_preview_before_blocking_model_returns(thread_timeout):
    from test_jobs import answer

    class Service:
        entered = threading.Event()
        release = threading.Event()

        def research_plan(self, request):
            return {'ready': True}

        def research(self, request, control):
            with research_scope(control):
                progress('verified_preview', '已缓存数字', preview={'schema': 'filingcopilot.financial-preview/v1', 'claims': []})
                self.entered.set()
                assert self.release.wait(thread_timeout)
            return answer(request)

    service = Service()
    jobs = ResearchJobs(service, workers=1)
    try:
        job = jobs.start(ResearchRequest(question='Apple stock', ticker='AAPL'))
        assert service.entered.wait(thread_timeout)
        running = jobs.get(job['id'])
        assert running['status'] == 'running' and running['result'] is None
        assert running['preview']['schema'] == 'filingcopilot.financial-preview/v1'
        assert all('preview' not in event for event in running['events'])
        running['preview']['claims'].append('changed')
        assert jobs.get(job['id'])['preview']['claims'] == []
    finally:
        service.release.set()
        jobs.close()


def test_cached_preview_uses_public_annual_facts_without_fetching(tools):
    request = ResearchRequest(question='苹果股票怎么样', ticker='AAPL', as_of=date(2025, 11, 1))
    preview = cached_financial_preview(tools.corpus, tools.retriever, request)
    assert preview and preview['claims']
    assert preview['freshness'] == 'cached_not_refreshed'
    assert all(e['filing']['filed'] <= request.as_of.isoformat() for e in preview['evidence'])
    assert cached_financial_preview(tools.corpus, tools.retriever, replace_request(request, 'FY2022 营收')) is None
    assert cached_financial_preview(tools.corpus, tools.retriever, replace_request(request, '最近季度营收')) is None
    assert cached_financial_preview(tools.corpus, tools.retriever, replace_request(request, 'TTM 现金流')) is None
    assert cached_financial_preview(tools.corpus, tools.retriever, replace_request(request, '微软 FY2025 营收')) is None
    assert cached_financial_preview(tools.corpus, tools.retriever, replace_request(request, '苹果和微软比较')) is None


def replace_request(request, question):
    return request.model_copy(update={'question': question})


def test_real_service_publishes_preview_before_source_work(tmp_path, monkeypatch):
    service = FilingCopilot(Settings(home=Path(tmp_path), embedding_mode='off'))
    request = ResearchRequest(question='苹果 FY2025 营收', ticker='AAPL', mode='reference', as_of=date(2025, 11, 1))
    events = []
    from filingcopilot.execution import ResearchControl
    control = ResearchControl(events.append)
    original = service._research

    def source_work(req):
        assert any(event.get('preview', {}).get('claims') for event in events)
        return original(req)

    monkeypatch.setattr(service, '_research', source_work)
    service.research(request, control=control)
