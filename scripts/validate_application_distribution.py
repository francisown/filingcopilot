"""Check current release wheel bytes and run an offline isolated application."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    mismatches = []
    with zipfile.ZipFile(args.wheel) as archive:
        expected = [path for path in (ROOT / 'src/filingcopilot').rglob('*')
                    if path.is_file() and '__pycache__' not in path.parts]
        for path in expected:
            name = path.relative_to(ROOT / 'src').as_posix()
            if name not in archive.namelist() or archive.read(name) != path.read_bytes():
                mismatches.append(name)
    if mismatches:
        raise ValueError('Wheel/source resource mismatch: '+', '.join(mismatches))
    target = ROOT / '.filingcopilot/application-isolation' / uuid.uuid4().hex
    install = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps',
                              '--target', str(target), str(args.wheel)],
                             capture_output=True, text=True)
    if install.returncode:
        raise ValueError('Local wheel installation failed')
    program = '''import json, socket, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import httpx
def offline(*args, **kwargs):
    raise AssertionError("Network must not run during this check")
socket.create_connection = offline
httpx.Client.request = offline
httpx.AsyncClient.request = offline
from filingcopilot import __version__
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings
service = FilingCopilot(Settings(home=Path(sys.argv[2]), embedding_mode="off"), gateway=False)
print(json.dumps({"version":__version__, "module":sys.modules["filingcopilot"].__file__,
    "currencies":{ticker:service.capabilities(ticker)["annual_reporting_currency"]
        for ticker in ("PDD","BABA","JD","TSM")}, "network_blocked":True}))
'''
    replay = subprocess.run([sys.executable, '-c', program, str(target), str(target/'data')],
                            capture_output=True, text=True)
    if replay.returncode:
        raise ValueError('Isolated import failed: '+replay.stderr)
    result = json.loads(replay.stdout)
    if not Path(result['module']).is_relative_to(target):
        raise ValueError('Import did not come from installed wheel')
    report = {'schema':'filingcopilot.application-wheel-check/v1',
              'wheel_sha256':hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
              'resources_checked':len(expected),'all_source_bytes_match':True,
              'offline_isolation':result}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    shutil.rmtree(target, ignore_errors=True)
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
