"""Replay original packages using only an installed independent verifier wheel."""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import json
import socket
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--home', type=Path, default=Path('.filingcopilot'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, help='Exact wheel whose installed target is being replayed')
    parser.add_argument('--evidence-root', type=Path, default=Path('artifacts'),
                        help='Retained original replay list and PDD/BABA/JD packages; may be outside the source repo')
    args = parser.parse_args()
    sys.path.insert(0, str(args.target.resolve()))

    class BlockApplication(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'filingcopilot', 'openai', 'langgraph', 'mcp', 'fastapi', 'fastembed'}:
                raise AssertionError('Application or model SDK import attempted')

    sys.meta_path.insert(0, BlockApplication())
    import httpx

    def offline(*args, **kwargs):
        raise AssertionError('Network access attempted')

    httpx.Client.request = offline
    httpx.AsyncClient.request = offline
    socket.create_connection = offline
    from filingcopilot_verify import __version__
    from filingcopilot_verify.audit import Package
    from filingcopilot_verify.cli import main as verify
    from filingcopilot_verify.corpus import Corpus

    corpus = Corpus(args.home, readonly=True)
    source = args.evidence_root / 'semantic-production-package-replay-2026-10-11.json'
    original = json.loads(source.read_text(encoding='utf-8'))
    replay_dir = args.target / 'package-replay'
    replay_dir.mkdir(exist_ok=True)
    rows, start = [], time.perf_counter()
    for item in original['rows']:
        package = corpus.run_package(item['run_id'])
        before = json.dumps(package, ensure_ascii=False, sort_keys=True)
        path = replay_dir / (item['run_id'] + '.json')
        path.write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8')
        report_path = replay_dir / (item['run_id'] + '-report.json')
        code = verify([str(path), '--corpus-home', str(args.home), '--source-cache', str(args.home / 'sec-cache'),
                       '--market-cache', str(args.home / 'market-cache'), '--external-cache', str(args.home / 'external-cache'),
                       '--output', str(report_path)])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        rows.append({'run_id': item['run_id'], 'exit_code': code, 'status': report['status'],
                     'original_unchanged': before == json.dumps(corpus.run_package(item['run_id']), ensure_ascii=False, sort_keys=True),
                     'failures': [c for c in report['checks'] if c['status'] == 'fail']})
    for ticker in ['pdd', 'baba', 'jd']:
        path = args.evidence_root / f'{ticker}-native-research-package-2026-10-11.json'
        before = path.read_bytes()
        report_path = replay_dir / (ticker + '-report.json')
        code = verify([str(path), '--source-cache', str(args.home / 'sec-cache'), '--output', str(report_path)])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        rows.append({'ticker': ticker, 'exit_code': code, 'status': report['status'],
                     'original_unchanged': path.read_bytes() == before, 'raw_sources': report['raw_sources'],
                     'failures': [c for c in report['checks'] if c['status'] == 'fail']})
    Path('docs/research-package-schema-v1.json').write_text(json.dumps(Package.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    wheel = args.wheel or Path(f'dist/filingcopilot_verify-{__version__}-py3-none-any.whl')
    report = {'schema': 'filingcopilot.independent-verifier-validation/v1',
              'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
              'verifier_version': __version__,
              'replayed': len(rows), 'invalid': sum(row['exit_code'] != 0 for row in rows),
              'all_originals_unchanged': all(row['original_unchanged'] for row in rows),
              'elapsed_seconds': round(time.perf_counter() - start, 3),
              'application_and_sdk_imports_blocked': True, 'network_blocked': True, 'rows': rows,
              'limitations': ['Offline numeric/source consistency, not human semantic or investment accuracy.',
                             'Dependencies come from the existing Python environment; the verifier never imports the main application.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('rows', 'limitations')}))
    raise SystemExit(1 if report['invalid'] else 0)


if __name__ == '__main__':
    main()
