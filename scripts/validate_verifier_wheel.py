"""Install the independent verifier wheel into an isolated target and smoke it."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    source = ROOT / "src" / "filingcopilot"
    version = next(
        ast.literal_eval(node.value)
        for node in ast.parse((source / "__init__.py").read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(item, ast.Name) and item.id == "__version__" for item in node.targets)
    )
    with zipfile.ZipFile(wheel) as archive:
        manifest = json.loads(archive.read("filingcopilot_verify/build-manifest.json"))
        metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("Verifier wheel must contain exactly one distribution metadata file")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        if metadata["Version"] != version or manifest["version"] != version:
            raise ValueError("Verifier metadata, manifest and source versions differ")
        for name, digest in manifest["source_sha256"].items():
            original = (source / name).read_bytes()
            if hashlib.sha256(original).hexdigest() != digest:
                raise ValueError(f"Verifier source digest mismatch: {name}")
            expected = original
            if name.endswith(".py"):
                expected = original.decode("utf-8").replace(
                    'files("filingcopilot")', 'files("filingcopilot_verify")'
                ).replace("files('filingcopilot')", "files('filingcopilot_verify')").encode("utf-8")
            if archive.read("filingcopilot_verify/" + name) != expected:
                raise ValueError(f"Verifier wheel resource mismatch: {name}")

    target = Path(tempfile.mkdtemp(prefix="filingcopilot-verifier-wheel-"))
    try:
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
            capture_output=True,
            text=True,
        )
        if install.returncode:
            raise RuntimeError(f"Verifier wheel installation failed: {install.stderr}")

        schema = target / "schema.json"
        probe = """
import importlib.abc
import json
import socket
import sys
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

class BlockApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'filingcopilot', 'openai', 'langgraph', 'mcp', 'fastapi', 'fastembed'}:
            raise AssertionError('application or model SDK imported')

sys.meta_path.insert(0, BlockApplication())
def offline(*args, **kwargs):
    raise AssertionError('network access attempted')
socket.create_connection = offline
import httpx
httpx.Client.request = offline
httpx.AsyncClient.request = offline
# Put the extracted target first even when the runner has an editable
# application install or a pre-existing PYTHONPATH entry.
sys.path.insert(0, sys.argv[2])
from filingcopilot_verify import __version__
import filingcopilot_verify
_module_file = Path(filingcopilot_verify.__file__).resolve()
_wheel_target = Path(sys.argv[2]).resolve()
assert _module_file.is_relative_to(_wheel_target), (str(_module_file), str(_wheel_target), sys.path[:6])
assert version('filingcopilot-verify') == __version__ == sys.argv[4]
from filingcopilot_verify.cli import main
from filingcopilot_verify.audit import digest
assert main(['--schema', '--output', sys.argv[1]]) == 0
payload = json.loads(open(sys.argv[1], encoding='utf-8').read())
assert payload['properties']['schema_version']['const'] == 'filingcopilot.research-package/v1'
example = Path(sys.argv[3])
before = example.read_bytes()
assert main([str(example), '--output', str(Path(sys.argv[2])/'valid-report.json')]) == 0
assert example.read_bytes() == before
invalid = deepcopy(json.loads(before))
fact = next(item for item in invalid['research']['evidence'] if item['kind'] == 'fact')
fact['value'] = '999'
invalid['manifest']['payload_sha256'] = digest({k: v for k,v in invalid.items() if k != 'manifest'})
tampered = Path(sys.argv[2])/'tampered.json'
tampered.write_text(json.dumps(invalid), encoding='utf-8')
assert main([str(tampered), '--output', str(Path(sys.argv[2])/'invalid-report.json')]) == 1
assert main([str(Path(sys.argv[2])/'missing.json')]) == 2
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(target)
        result = subprocess.run(
            [sys.executable, "-c", probe, str(schema), str(target),
             str(ROOT / "examples" / "annual-facts.json"), version],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(f"Verifier wheel smoke test failed: {result.stderr}")

        report = {
            "schema": "filingcopilot.verifier-wheel-check/v1",
            "wheel": wheel.name,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "version": version,
            "resources_checked": len(manifest["source_sha256"]),
            "schema_generated": schema.is_file(),
            "valid_example_accepted": True,
            "resealed_tampered_fact_rejected": True,
            "unreadable_input_exit_code": 2,
            "application_and_sdk_imports_blocked": True,
            "network_blocked": True,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        shutil.rmtree(target, ignore_errors=True)


if __name__ == "__main__":
    main()
