import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_verifier_distribution import prepare_distribution, wheel_build_command

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_verifier_wheel_build_uses_declared_isolated_backend():
    command = wheel_build_command("python", Path("dist"), Path("build"))
    assert command[:4] == ["python", "-m", "pip", "wheel"]
    assert "--no-build-isolation" not in command


def test_offline_distribution_imports_without_application_or_model_sdks(tmp_path):
    directory = tmp_path / "distribution"
    manifest = prepare_distribution(directory)
    assert not {"agent.py", "service.py", "api.py"} & manifest["source_sha256"].keys()
    probe = r'''
import importlib.abc, json, sys
class BlockApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'filingcopilot', 'openai', 'langgraph', 'fastapi', 'mcp', 'fastembed'}:
            raise AssertionError('offline verifier imported application or model: ' + fullname)
sys.meta_path.insert(0, BlockApplication())
import httpx
def no_network(*args, **kwargs):
    raise AssertionError('offline verifier attempted network access')
httpx.Client.request = no_network
httpx.AsyncClient.request = no_network
from filingcopilot_verify.cli import main
assert main(['--schema', '--output', sys.argv[1]]) == 0
from filingcopilot_verify.audit import audit_package
assert audit_package({})['status'] == 'invalid'
assert main([sys.argv[1] + '.absent']) == 2
print('offline independent imports and schema passed')
'''
    output = tmp_path / "schema.json"
    result = subprocess.run([sys.executable, "-c", probe, str(output)], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(directory / "src")},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    schema = json.loads(output.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "filingcopilot.research-package/v1"


def test_distribution_manifest_matches_main_auditor_bytes(tmp_path):
    import hashlib

    from filingcopilot import __version__

    manifest = prepare_distribution(tmp_path / "distribution")
    assert manifest['version'] == __version__
    original = Path(__file__).resolve().parents[1] / "src" / "filingcopilot"
    for name, expected in manifest["source_sha256"].items():
        assert hashlib.sha256((original / name).read_bytes()).hexdigest() == expected


def test_independent_cli_recomputes_and_rejects_resealed_calculation(tmp_path):
    from copy import deepcopy

    from filingcopilot.audit import digest
    from filingcopilot.models import ResearchRequest
    from filingcopilot.service import FilingCopilot
    from filingcopilot.settings import Settings

    service = FilingCopilot(Settings(home=tmp_path / "corpus", embedding_mode="off"), gateway=False)
    answer = service.research(ResearchRequest(question="苹果 FY2025 营收同比和毛利率", ticker="AAPL", as_of="2026-10-08", mode="reference"))
    package = service.export_package(answer.id)
    invalid = deepcopy(package)
    calculation = next(item for item in invalid['research']['evidence'] if item['kind'] == 'calculation')
    calculation['value'] = '999'
    invalid['manifest']['payload_sha256'] = digest({key: value for key, value in invalid.items() if key != 'manifest'})
    directory = tmp_path / "distribution"
    prepare_distribution(directory)
    for filename, data, expected in [("valid.json", package, 0), ("tampered.json", invalid, 1)]:
        path = tmp_path / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        before = path.read_bytes()
        result = subprocess.run([sys.executable, "-m", "filingcopilot_verify.cli", str(path)],
                                cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(directory / "src")},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == expected, result.stderr
        assert json.loads(result.stdout)['status'] == ('consistent' if expected == 0 else 'invalid')
        assert path.read_bytes() == before
    database = service.corpus.path
    before = database.read_bytes()
    valid = tmp_path / 'valid.json'
    checked = subprocess.run([sys.executable, '-m', 'filingcopilot_verify.cli', str(valid),
                              '--corpus-home', str(database.parent)], cwd=tmp_path,
                             env={**os.environ, 'PYTHONPATH': str(directory / 'src')},
                             capture_output=True, text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)['source_validation'] == 'matched'
    assert database.read_bytes() == before
