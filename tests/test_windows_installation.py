import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def installer(monkeypatch, arguments=(), python=(3, 12)):
    spec = importlib.util.spec_from_file_location("test_installer", ROOT / "scripts/install_dependencies.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "argv", ["install_dependencies.py", *arguments])
    monkeypatch.setattr(module.sys, "version_info", python)
    monkeypatch.setattr(module.urllib.request, "getproxies", lambda: {})
    wheel = b"controlled wheel bytes, not an installable package"
    metadata = {"urls": [{"filename": "pip-test-py3-none-any.whl", "url": "https://example.invalid/pip.whl",
                          "digests": {"sha256": hashlib.sha256(wheel).hexdigest()}}]}
    responses = iter([json.dumps(metadata).encode(), wheel])
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(next(responses)))
    calls = []
    monkeypatch.setattr(module.subprocess, "check_call", lambda command, **kw: calls.append(command))
    return module, calls


@pytest.mark.parametrize("python", [(3, 10), (3, 12), (3, 13)])
def test_default_install_is_core_on_supported_python(monkeypatch, python):
    module, calls = installer(monkeypatch, python=python)
    module.main()
    assert len(calls) == 2
    assert calls[0][4] == "--no-index"
    assert calls[1][-2:] == ["-e", "."]
    assert not any("requirements-lock.txt" in command for command in calls)
    assert not any("semantic" in part for command in calls for part in command)


@pytest.mark.parametrize("language,expected", [("en", "Checking the official pip installer metadata"), ("zh", "正在检查 PyPI")])
def test_installer_reports_network_and_install_phases(monkeypatch, capsys, language, expected):
    module, _ = installer(monkeypatch, ["--language", language])
    module.main()
    output = capsys.readouterr().out
    assert expected in output
    assert ("Core installation complete." if language == "en" else "核心安装完成") in output


def test_historical_lock_is_an_explicit_python_310_option(monkeypatch):
    module, calls = installer(monkeypatch, ["--profile", "locked"], python=(3, 10))
    module.main()
    assert len(calls) == 3 and "requirements-lock.txt" in calls[1]
    assert "--no-deps" in calls[2]


@pytest.mark.parametrize("language", ["en", "zh"])
def test_incompatible_lock_fails_before_any_install(monkeypatch, capsys, language):
    module, calls = installer(monkeypatch, ["--profile", "locked", "--language", language])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2 and not calls
    assert ("historical lock" if language == "en" else "历史锁定依赖") in capsys.readouterr().err


def test_pip_digest_mismatch_blocks_install(monkeypatch):
    module, calls = installer(monkeypatch)
    monkeypatch.setattr(module.hashlib, "sha256", lambda _: type("Digest", (), {"hexdigest": lambda self: "wrong"})())
    with pytest.raises(RuntimeError, match="digest mismatch"):
        module.main()
    assert not calls


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launchers require PowerShell")
@pytest.mark.slow
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_windows_interpreter_selection_without_installation(shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip("Optional PowerShell host is unavailable")
    result = subprocess.run([executable, "-NoProfile", "-File", str(ROOT / "scripts/check_windows_python.ps1"),
                             "-ProjectRoot", str(ROOT)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No installation or service startup" in result.stdout


def test_serve_default_matches_readme_without_starting_server(monkeypatch):
    import uvicorn

    from filingcopilot import api, cli

    calls = []
    monkeypatch.setattr(sys, "argv", ["filingcopilot", "serve"])
    monkeypatch.setattr(cli, "FilingCopilot", lambda _: object())
    monkeypatch.setattr(api, "create_app", lambda _: "controlled-app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    cli.main()
    assert calls == [("controlled-app", {"host": "127.0.0.1", "port": 8159})]
