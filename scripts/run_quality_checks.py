"""Named test profiles with exact commands, environment and wall-time records.

Unit selects unmarked rule tests; full/regression collect the entire suite.
The legacy fast name is an alias, not a wall-time promise on any machine.
Live SEC/index/model evaluations are separate opt-in commands, not hidden in
either profile. No production service is started or stopped by this runner.
"""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import uuid
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_tree(path: Path) -> None:
    """Remove a managed fixture tree, including read-only files on Windows.

    The public-snapshot tests create a temporary Git repository. Git marks
    object files read-only on Windows; a later quality-profile invocation must
    be able to clear that disposable tree before creating a fresh one.
    """
    if not path.exists():
        return

    def onerror(function, target, _exc_info):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=onerror)

def python_commands(profile, run_home, cache_state='both'):
    """Independent fixture namespaces, shared runner cache only for a warm replay."""
    base = [sys.executable, '-m', 'pytest', '-q', '--durations=30']
    if profile in {'unit', 'fast'}:
        return [('unit', [*base, '-m', 'not slow'])]
    if profile == 'regression':
        return [('as_is', base)]
    states = ['cold', 'warm'] if cache_state == 'both' else [cache_state]
    return [(state, [*base, '--basetemp', str(run_home/(state+'-fixtures')),
                     '-o', 'cache_dir='+str(run_home/'pytest-cache')]) for state in states]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=['unit', 'fast', 'full', 'regression', 'frontend'], required=True)
    parser.add_argument('--cache-state', choices=['both','cold','warm'], default='both')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    requested_profile = args.profile
    if args.profile == 'fast':
        print('The legacy fast profile is an alias for unit; speed is relative to the full collection.', file=sys.stderr)
        args.profile = 'unit'
    # Test fixtures are disposable. Reusing a single managed namespace keeps
    # repeated hourly runs from accumulating copied SQLite databases, SEC
    # caches and model/vector fixtures under .filingcopilot.
    run_id = uuid.uuid4().hex[:10]
    run_home = ROOT/'.filingcopilot'/'test-runs'/'current'
    if run_home.exists():
        remove_tree(run_home)
    run_home.mkdir(parents=True, exist_ok=True)
    if args.profile == 'frontend':
        node = shutil.which('node')
        if not node:
            raise ValueError('Node is required for frontend checks')
        commands = [[node, '--check', str(p.relative_to(ROOT))] for p in sorted((ROOT/'src/filingcopilot/static').glob('*.js'))]
        commands += [[node, str(p.relative_to(ROOT))] for p in sorted((ROOT/'scripts').glob('check_*.cjs'))]
        commands = [('frontend', command) for command in commands]
    else:
        commands = python_commands(args.profile, run_home, args.cache_state)
    rows = []
    started = time.perf_counter()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for i, (state, command) in enumerate(commands):
        stamp = time.perf_counter()
        log = args.output.parent / f'{args.profile}-{run_id}-{i+1}.txt'
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        rows.append({'command': command, 'exit_code': result.returncode,
                     'cache_state':state,
                     'timing_scope':'Fresh isolated fixture/application directories and empty pytest cache; installed dependencies/model weights already exist; OS file cache uncontrolled.' if state=='cold' else 'Immediate rerun with prior pytest/host cache; fixtures/application directories remain fresh.' if state=='warm' else 'Existing host caches; fresh Python process.',
                     'seconds': round(time.perf_counter()-stamp, 3), 'log': str(log)})
        print(json.dumps(rows[-1]), flush=True)
    report = {'schema': 'filingcopilot.quality-profile/v2', 'profile': args.profile,
              'requested_profile': requested_profile,
              'passed': all(r['exit_code']==0 for r in rows), 'commands': rows,
              'total_seconds': round(time.perf_counter()-started, 3),
              'environment': {'python': platform.python_version(), 'interpreter': sys.executable,
                              'os': platform.platform(), 'pytest': version('pytest'), 'httpx': version('httpx')},
              'scope': 'pytest -m not slow; unit/rule subset relative to the full collection, not an absolute speed guarantee; deselection count retained in log' if args.profile=='unit' else 'complete Python test collection' if args.profile in {'regression','full'} else 'JS syntax and controlled UI harnesses',
              'limitations': ['No cold download, real model reasoning or investment accuracy measured.',
                              'Fresh process; fixture databases in pytest temporary directories. Existing host/dependency caches not cleared.',
                              'Shared Windows host; not a latency guarantee on other machines.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
