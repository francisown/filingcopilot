"""Real SEC first-acquisition timing in a fresh corpus/vector/cache directory.

Uses existing local model weights, never credentials/model reasoning. This is
an in-process API check, not a production web launch or browser acceptance.
"""

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from filingcopilot.api import create_app
from filingcopilot.audit import audit_package
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path('.filingcopilot'))
    parser.add_argument('--ticker', default='PDD')
    parser.add_argument('--as-of', default='2026-10-11')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--direct-sec', action='store_true', help='Opt in to direct SEC HTTPS in this process only')
    parser.add_argument('--keep-scope', action='store_true', help='Keep the temporary replay corpus for forensic inspection')
    args = parser.parse_args()
    configured = Settings.from_env()
    if args.direct_sec:
        os.environ['NO_PROXY'] = 'www.sec.gov,data.sec.gov,127.0.0.1,localhost'
        os.environ['no_proxy'] = os.environ['NO_PROXY']
    scope = args.home / 'cold-preparation-replays' / uuid.uuid4().hex
    scope.mkdir(parents=True)
    weights = args.home / 'models' / 'bge-local'
    if not (weights / 'model_optimized.onnx').is_file():
        raise ValueError('Existing BGE weights are required; refuse a download in this replay')
    service = FilingCopilot(Settings(home=scope, sec_user_agent=configured.sec_user_agent))
    # The replay only reads the verified host snapshot.  Copying the ONNX
    # directory into every UUID scope multiplied the workspace by gigabytes.
    # Retriever.model_path is explicit and read-only; a forensic copy remains
    # available through --keep-scope when needed.
    service.retriever.model_path = weights.resolve()
    client = TestClient(create_app(service))
    started = time.perf_counter()
    snapshots = []
    previous = None
    first_visible = None
    try:
        job = client.post('/api/companies/prepare', json={'ticker': args.ticker, 'as_of': args.as_of, 'years': 2}).json()
        while True:
            job = client.get('/api/companies/jobs/'+job['id']).json()
            if job.get('preview') and first_visible is None:
                first_visible = round((time.perf_counter()-started)*1000, 3)
            key = (job['stage'], job['status'], json.dumps(job.get('progress', {}), sort_keys=True))
            if key != previous:
                snapshots.append({'elapsed_ms': round((time.perf_counter()-started)*1000, 3),
                                  'stage': job['stage'], 'status': job['status'],
                                  'counts': job.get('progress'), 'has_preview': bool(job.get('preview'))})
                print(json.dumps(snapshots[-1]), flush=True)
                previous = key
            if job['status'] != 'running' or time.perf_counter()-started > 480:
                break
            time.sleep(.1)
        elapsed = round(time.perf_counter()-started, 3)
        report = {'ticker': args.ticker, 'as_of': args.as_of, 'scope': str(scope),
                  'scope_retained': args.keep_scope,
                  'timing_scope': 'empty source/corpus/vector cache; existing BGE weights; in-process API polling 100ms; no LLM',
                  'direct_sec': args.direct_sec, 'status': job['status'],
                  'first_numbers_server_ms': job.get('first_numbers_ms'), 'first_numbers_api_ms': first_visible,
                  'total_seconds': elapsed, 'first_numbers_under_20s': first_visible is not None and first_visible < 20000,
                  'snapshots': snapshots, 'error': job.get('error'),
                  'sources': job.get('result', {}).get('filings', []) if job.get('result') else []}
        # Preserve measured acquisition even if later audit validation fails.
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if job['status'] == 'ready':
            question = f"{args.ticker} FY{job['preview']['fiscal_year']} 营业收入和净利润"
            answer = service.research(ResearchRequest(question=question, ticker=args.ticker, as_of=args.as_of, mode='reference'))
            package = service.export_package(answer.id)
            audit = audit_package(package, source_cache=scope/'sec-cache', corpus=service.corpus, trusted_domain=service.domain)
            report['raw_audit'] = {'status': audit['status'], 'raw_sources': audit['raw_sources']}
            report['package_sha256'] = hashlib.sha256(json.dumps(package, sort_keys=True).encode()).hexdigest()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if job['status'] != 'ready':
            raise SystemExit(2)
    finally:
        service.discovery.executor.shutdown(wait=True)
        if not args.keep_scope:
            import shutil
            shutil.rmtree(scope, ignore_errors=True)


if __name__ == '__main__':
    main()
