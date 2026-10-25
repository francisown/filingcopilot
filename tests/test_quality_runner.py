from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "quality_runner", ROOT / "scripts" / "run_quality_checks.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_runs_use_unique_cleaned_fixture_directories(tmp_path, monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    commands = []

    def fake_run(command, **kwargs):
        commands.append((list(command), kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.platform, "platform", lambda: "test-platform")
    output = tmp_path / "reports" / "full.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_quality_checks.py",
            "--profile",
            "full",
            "--cache-state",
            "both",
            "--output",
            str(output),
        ],
    )

    run_root = tmp_path / ".filingcopilot" / "test-runs"

    with pytest.raises(SystemExit) as first:
        runner.main()
    assert first.value.code == 0
    first_paths = {
        argument
        for command, _ in commands
        for argument in command
        if Path(argument).is_relative_to(run_root)
    }
    assert len(first_paths) == 2
    assert not any(Path(path).exists() for path in first_paths)

    commands.clear()
    with pytest.raises(SystemExit) as second:
        runner.main()
    assert second.value.code == 0
    second_paths = {
        argument
        for command, _ in commands
        for argument in command
        if Path(argument).is_relative_to(run_root)
    }
    assert len(second_paths) == 2
    assert first_paths.isdisjoint(second_paths)
    assert not any(Path(path).exists() for path in second_paths)


def test_quality_runner_cleans_up_after_failure(tmp_path, monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    seen = []

    def fake_run(command, **kwargs):
        seen.extend(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.platform, "platform", lambda: "test-platform")
    output = tmp_path / "reports" / "unit.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_quality_checks.py", "--profile", "unit", "--output", str(output)],
    )

    with pytest.raises(SystemExit) as result:
        runner.main()
    assert result.value.code == 1
    run_root = tmp_path / ".filingcopilot" / "test-runs"
    assert not any(run_root.iterdir()) if run_root.exists() else True
