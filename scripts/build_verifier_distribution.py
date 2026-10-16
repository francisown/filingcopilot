"""Build the offline verifier from the same source as the application.

No publication or network operation is performed. Every build has a fresh
directory and a module/source digest manifest, avoiding a second handwritten
implementation of the audit rules.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"agent", "api", "service", "cli", "mcp_server", "__main__"}
RESOURCES = ("domain-defaults.json", "stock-directory.json", "peer-groups.json", "locale-en.json")


def module_closure(source: Path) -> list[str]:
    pending, visited = ["audit", "verification_badge"], set()
    while pending:
        name = pending.pop()
        if name in visited or name in EXCLUDED:
            continue
        path = source / f"{name}.py"
        if not path.is_file():
            raise ValueError(f"missing offline dependency: {name}")
        visited.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                pending.append(node.module.split(".")[0])
    return sorted(visited)


def prepare_distribution(directory: Path, *, source: Path | None = None) -> dict:
    source = source or ROOT / "src" / "filingcopilot"
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / "src" / "filingcopilot_verify"
    target.mkdir(parents=True)
    hashes = {}
    for name in module_closure(source):
        original = (source / f"{name}.py").read_bytes()
        hashes[f"{name}.py"] = hashlib.sha256(original).hexdigest()
        # Package-relative resource lookup must resolve inside this wheel.
        text = original.decode("utf-8").replace('files("filingcopilot")', 'files("filingcopilot_verify")')
        text = text.replace("files('filingcopilot')", "files('filingcopilot_verify')")
        # Byte-exact write: text mode translates newlines on Windows and would
        # break the source_sha256 manifest recorded above.
        (target / f"{name}.py").write_bytes(text.encode("utf-8"))
    for name in RESOURCES:
        shutil.copyfile(source / name, target / name)
        hashes[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
    version_text = (source / "__init__.py").read_text(encoding="utf-8")
    (target / "__init__.py").write_text(version_text, encoding="utf-8")
    shutil.copyfile(ROOT / "packages" / "verifier" / "cli.py", target / "cli.py")
    version = next(ast.literal_eval(node.value) for node in ast.parse(version_text).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
    manifest = {"schema": "filingcopilot.verifier-build/v1", "version": version,
                "source_sha256": hashes, "excluded_application_modules": sorted(EXCLUDED),
                "tooling_source_sha256": {
                    '__init__.py': hashlib.sha256((source/'__init__.py').read_bytes()).hexdigest(),
                    'packages/verifier/cli.py': hashlib.sha256((ROOT/'packages/verifier/cli.py').read_bytes()).hexdigest(),
                }}
    (target / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (directory / "pyproject.toml").write_text(f'''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
[project]
name = "filingcopilot-verify"
version = "{version}"
description = "Offline financial research package consistency and raw-source checks"
requires-python = ">=3.10"
dependencies = ["beautifulsoup4>=4.12,<5", "httpx>=0.27,<1", "numpy>=1.26,<3", "pydantic>=2.10,<3", "python-dotenv>=1,<2", "tzdata>=2025.1"]
[project.scripts]
filingcopilot-verify = "filingcopilot_verify.cli:main"
[tool.setuptools.packages.find]
where = ["src"]
[tool.setuptools.package-data]
filingcopilot_verify = ["*.json"]
''', encoding="utf-8")
    return manifest


def wheel_build_command(python: str, output: Path, directory: Path) -> list[str]:
    """Return the isolated pip build command used by the CLI entry point."""
    return [python, "-m", "pip", "wheel", "--no-deps",
            "--wheel-dir", str(output.resolve()), str(directory)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    directory = ROOT / ".filingcopilot" / "verifier-build" / uuid.uuid4().hex
    manifest = prepare_distribution(directory)
    args.output.mkdir(parents=True, exist_ok=True)
    # Keep build isolation enabled.  Python 3.12 virtual environments no
    # longer include setuptools by default, so relying on a runner's ambient
    # ``setuptools.build_meta`` makes this command fail before wheel building
    # starts.  The temporary build environment is supplied from the
    # ``build-system.requires`` declaration above and is discarded by pip.
    subprocess.run(wheel_build_command(sys.executable, args.output, directory), check=True)
    shutil.rmtree(directory, ignore_errors=True)
    print(json.dumps({"version": manifest["version"], "modules": len(manifest["source_sha256"]) - len(RESOURCES),
                      "distribution_directory": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
