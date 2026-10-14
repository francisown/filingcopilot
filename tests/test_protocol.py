import json

import pytest

from filingcopilot.cli import main
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


@pytest.mark.parametrize("tampered", [False, True])
def test_standalone_verifier_needs_no_service_or_config(tmp_path, monkeypatch, capsys, tampered):
    service = FilingCopilot(Settings(home=tmp_path / "corpus", embedding_mode="off"))
    answer = service.research(
        ResearchRequest(
            question="苹果 FY2025 营收", ticker="AAPL", as_of="2026-10-08", mode="reference"
        )
    )
    package = service.export_package(answer.id)
    if tampered:
        package["research"]["question"] += "tampered"
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "audit.json"
    monkeypatch.setattr(
        "sys.argv", ["filingcopilot", "verify-package", str(path), "--output", str(output)]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Standalone verification must not load model or service settings")

    monkeypatch.setattr(Settings, "from_env", forbidden)
    if tampered:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
    else:
        main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == ("invalid" if tampered else "consistent")
    assert report["source_validation"] == "not_requested"
    assert json.loads(capsys.readouterr().out)["status"] == report["status"]
