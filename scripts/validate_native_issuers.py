"""Replay cached native 20-F originals and time source-bound annual previews."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path

from filingcopilot.audit import audit_package
from filingcopilot.models import ResearchRequest
from filingcopilot.preview import cached_financial_preview
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path('.filingcopilot'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    service = FilingCopilot(Settings(home=args.home.resolve(), embedding_mode='off'), gateway=False)
    rows = []
    for ticker in ['PDD', 'BABA', 'JD']:
        catalog = service.corpus.catalog(date(2026, 10, 11), ticker)
        year = max(int(f['period_end'][:4]) for f in catalog if f['form'] == '20-F')
        request = ResearchRequest(question=f'{ticker} FY{year} 营业收入和净利润同比增长',
                                  ticker=ticker, as_of='2026-10-11', mode='reference')
        latencies = []
        for _ in range(20):
            start = time.perf_counter()
            preview = cached_financial_preview(service.corpus, service.retriever, request)
            latencies.append((time.perf_counter() - start) * 1000)
        assert preview and preview['freshness'] == 'cached_not_refreshed'
        assert all(e['unit'] == 'CNY' for e in preview['evidence'])
        start = time.perf_counter()
        service.enrich_cached_facts(ticker)
        answer = service.research(request)
        package = service.export_package(answer.id)
        package_path = args.output.parent / f'{ticker.lower()}-native-research-package-2026-10-11.json'
        package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        before = package_path.read_bytes()
        report = audit_package(package, corpus=service.corpus, source_cache=args.home / 'sec-cache')
        assert report['status'] == 'consistent', [c for c in report['checks'] if c['status'] == 'fail']
        assert report['raw_sources']['facts_checked'] and not report['raw_sources']['missing']
        assert package_path.read_bytes() == before
        rows.append({'ticker': ticker, 'fiscal_year': year, 'currency': 'CNY',
                     'preview_p95_ms': round(sorted(latencies)[19], 3),
                     'preview_claims': len(preview['claims']), 'first_preview_ms': round(latencies[0], 3),
                     'research_and_raw_audit_seconds': round(time.perf_counter() - start, 3),
                     'answer': answer.model_dump(mode='json')['answer'], 'gaps': answer.gaps,
                     'raw_audit': {k: v for k, v in report.items() if k != 'checks'},
                     'package_sha256': hashlib.sha256(before).hexdigest(), 'package_path': str(package_path)})
        print(json.dumps({k: v for k, v in rows[-1].items() if k not in ('answer', 'raw_audit')}, ensure_ascii=False), flush=True)
    result = {'schema': 'filingcopilot.native-issuer-acceptance/v1', 'rows': rows,
              'limitations': ['Cached annual preview is historical context, not confirmation of the latest disclosure.',
                             'No LLM or network is called; fresh company acquisition was measured separately.',
                             'Consolidated native CNY amounts cannot be combined with USD ADS prices for P/E.',
                             'Mechanical raw-byte checks are not human semantic or investment accuracy.']}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
