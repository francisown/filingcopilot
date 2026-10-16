"""Aggregate measured third-review acceptance without inventing semantic labels."""

import hashlib
import json
import re
import statistics
import subprocess
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / 'artifacts' / name).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    full = read('third-round-full-comparison-final-1.0.5.json')
    fast = read('third-round-fast-comparison-final-1.0.5.json')
    frontend = read('third-round-frontend-release-final-1.0.5.json')
    assert full['passed'] and fast['passed'] and frontend['passed']
    for entry in full['commands'] + fast['commands']:
        log = (ROOT / entry['log']).read_text(encoding='utf-8')
        entry['tests_passed'] = int(re.findall(r'(\d+) passed', log)[-1])
    assert len(full['commands']) == 2
    assert full['commands'][0]['tests_passed'] == full['commands'][1]['tests_passed']
    rows = read('third-round-paired-final-2026-10-11.json')
    assert len(rows) == len({(row['id'], row['arm']) for row in rows}) == 40
    sentence_review = read('third-round-paired-citation-report-2026-10-11.json')
    assert sentence_review['integrity'] and sentence_review['status'] == 'awaiting_human_sentence_labels'
    manifest = read('third-round-paired-final-2026-10-11.manifest.json')
    comparison = {}
    for arm in ('filingcopilot', 'finrobot_analysis'):
        selected = [row for row in rows if row['arm'] == arm]
        usage = [row['answer'].get('usage', {}) if isinstance(row['answer'], dict)
                 else row.get('usage', {}) for row in selected]
        input_tokens = sum(value.get('prompt_tokens', value.get('input_tokens', 0)) for value in usage)
        output_tokens = sum(value.get('completion_tokens', value.get('output_tokens', 0)) for value in usage)
        # The pinned comparator adapter returns zeros despite nonempty prompts
        # and answers. Keep the raw rows, but do not interpret that as free usage.
        usable_tokens = bool(input_tokens and output_tokens)
        comparison[arm] = {'outputs': len(selected),
                           'delivery_status_counts': dict(Counter(row['status'] for row in selected)),
                           'total_seconds': round(sum(row.get('seconds', 0) for row in selected), 3),
                           'median_seconds_including_failures': round(statistics.median(row.get('seconds', 0) for row in selected), 3),
                           'input_tokens_reported': input_tokens if usable_tokens else None,
                           'output_tokens_reported': output_tokens if usable_tokens else None,
                           'successful_model_calls_reported': sum(value.get('model_calls', value.get('requests', 0)) for value in usage),
                           'usage_scope': 'Successful response usage only; timeout/retry billing may be unreported. Comparator adapter token zeros are unusable and represented as null, not free usage. No monetary cost inference. Execution sums exclude inter-arm pacing and setup.',
                           'citation_support_rate': None}
    preservation = read('third-round-preservation-2026-10-11.json')
    assert sha(ROOT / '.env') == preservation['env_sha256']
    preservation['env_unchanged'] = True
    for name in ('semantic-blind-claims-2026-10-11.json',
                 'semantic-production-blind-claims-2026-10-11.json'):
        path = ROOT / 'artifacts' / name
        raw = path.read_bytes()
        prior = subprocess.check_output(['git', 'show', 'HEAD:artifacts/'+name], cwd=ROOT)
        claims = json.loads(raw)
        assert json.loads(raw) == json.loads(prior)
        assert not any(claim.get('label') or claim.get('human_reviewer') for claim in claims)
        preservation['semantic_worksheets'][name] = {
            'sha256': sha(path), 'items': len(claims), 'nonempty_labels': 0,
            'parsed_content_identical_to_prior_commit': True,
            'bytes_after_lf_normalization_identical': raw.replace(b'\r\n', b'\n') == prior.replace(b'\r\n', b'\n'),
            'byte_comparison_note': 'Git blob uses LF; existing Windows working tree uses CRLF. No fields or labels changed.'}
    (ROOT / 'artifacts/third-round-preservation-2026-10-11.json').write_text(
        json.dumps(preservation, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    health = httpx.get('http://127.0.0.1:8159/api/health', timeout=10).json()
    assert health['version'] == '1.0.5' and not health['runtime']['needs_restart']
    from filingcopilot.runtime_identity import RuntimeIdentity
    assert RuntimeIdentity('1.0.5').fingerprint == health['runtime']['source_fingerprint']
    main_wheel = read('third-round-main-wheel-acceptance-1.0.5.json')
    verifier = read('third-round-verifier-acceptance-1.0.5.json')
    assert main_wheel['all_source_bytes_match'] and verifier['invalid'] == 0
    assert main_wheel['wheel_sha256'] == sha(ROOT / 'dist/filingcopilot-1.0.5-py3-none-any.whl')
    assert verifier['wheel_sha256'] == sha(ROOT / 'dist/filingcopilot_verify-1.0.5-py3-none-any.whl')
    report = {'schema': 'filingcopilot.third-round-release-validation/v1', 'version': '1.0.5',
              'parent_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'fast': fast, 'full': full,
              'frontend': {'passed': True, 'commands': len(frontend['commands']),
                           'artifact': 'third-round-frontend-release-final-1.0.5.json',
                           'scope': frontend['scope']},
              'mcp': {'transports': ['stdio', 'streamable-http'], 'tools': 30,
                      'log': 'third-round-mcp-release-final-1.0.5.txt',
                      'scope': 'Synthetic/offline protocol checks; not live provider or model quality.'},
              'fixed_evaluation': {key: read('third-round-evaluation-1.0.5.json')[key]
                                   for key in ('passed', 'total', 'scope')},
              'cold_company_preparation': {ticker: {key: read(f'third-round-cold-{ticker}-2026-10-11.json')[key]
                  for key in ('total_seconds', 'first_numbers_api_ms', 'timing_scope', 'status')}
                  for ticker in ('PDD', 'BABA')},
              'embedding_probe': 'third-round-embedding-probe-2026-10-11.json',
              'retrieval_baseline': 'third-round-retrieval-baseline-2026-10-11.json',
              'paired_configuration': {'model': manifest['model'], 'review_model': manifest['review_model'],
                  'budgets': manifest['budgets'], 'finrobot_revision': manifest['finrobot_revision'],
                  'source_zip_sha256': sha(ROOT / 'artifacts/third-round-paired-final-2026-10-11.manifest.source.zip'),
                  'arms': comparison, 'human_semantic_status': 'awaiting_human_sentence_labels',
                  'sentence_review': {'artifact': 'third-round-paired-citation-report-2026-10-11.json',
                      'units': sum(value['sentences'] for value in sentence_review['arms'].values()),
                      'human_labels': 0, 'citation_support_rates': None},
                  'scope': 'Bounded citation traceability comparison; delivery counts are not semantic support or product superiority.'},
              'main_wheel': main_wheel, 'independent_verifier': verifier,
              'runtime': health['runtime'], 'runtime_url': 'http://127.0.0.1:8159/',
              'native_eps': 'third-round-native-eps-real-2026-10-11.json',
              'human_labels': {'original_20': 0, 'new_40': 0, 't5_status': 'awaiting_human'},
              'remaining': ['T4 human sentence support labels', 'T5 human semantic labels',
                  'T7 native ordinary EPS to USD ADS audited conversion', 'Canadian 40-F integration outside Chinese 20-F scope'],
              'preservation': 'third-round-preservation-2026-10-11.json',
              'final_focused_regression': {'passed': 10, 'seconds': 0.16,
                  'log': 'third-round-comparison-completion-2026-10-11.txt',
                  'scope': 'After final sentence/context integrity edits; not a repeat of the complete suite.'},
              'final_ruff': {'passed': True, 'log': 'third-round-ruff-completion-2026-10-11.txt'},
              'diff_review': {'application_scripts_tests_docs_check': 'passed',
                  'raw_artifact_logs': 'Retained verbatim. git diff --check reports original trailing whitespace in failed pytest output and browser AX logs; no log normalization performed.'},
              'temporary_cleanup': 'third-round-temporary-cleanup-2026-10-11.json',
              'limitations': ['Installed dependency/weight and uncontrolled OS caches; not clean-machine test timing.',
                  'Same cheap primary/review model; no independent-model review claim.',
                  'Local engineering checks are not model or investment accuracy; no remote CI run.']}
    (ROOT / 'artifacts/third-round-release-validation-1.0.5.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'version': report['version'], 'full_tests': full['commands'][0]['tests_passed'],
                      'paired_outputs': len(rows), 'human_labels': 0}))


if __name__ == '__main__':
    main()
