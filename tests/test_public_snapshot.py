import hashlib
import json
import runpy
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

BUILDER = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build_public_snapshot.py"))


def png_with_text(kind=None, text=b""):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    # Pixel bytes can coincidentally resemble a drive path; they are not textual metadata.
    pixels = b"\x00" + (b"E:" + b"/pixel-colors" + bytes(48))[:48]
    image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 1, 8, 2, 0, 0, 0))
    if kind:
        body = b"Note\0" + text if kind == b"tEXt" else (
            b"Note\0\0" + zlib.compress(text) if kind == b"zTXt" else
            b"Note\0\1\0\0\0" + zlib.compress(text)
        )
        image += chunk(kind, body)
    return image + chunk(b"IDAT", zlib.compress(pixels, level=0)) + chunk(b"IEND", b"")


def test_public_snapshot_allows_real_png_pixel_bytes(tmp_path):
    path = tmp_path / "workspace.png"
    path.write_bytes(png_with_text())
    assert BUILDER["MACHINE_PATH"].search(path.read_bytes())
    BUILDER["validate_inputs"]([path], tmp_path, set())


@pytest.mark.parametrize("kind", [b"tEXt", b"zTXt", b"iTXt"])
def test_public_snapshot_rejects_paths_in_plain_or_compressed_png_metadata(tmp_path, kind):
    path = tmp_path / "workspace.png"
    path.write_bytes(png_with_text(kind, b"E:" + b"/private-project"))
    with pytest.raises(ValueError, match="Absolute machine path") as failure:
        BUILDER["validate_inputs"]([path], tmp_path, set())
    assert "private-project" not in str(failure.value)


def test_public_snapshot_rejects_configured_secret_in_compressed_png_metadata(tmp_path):
    path = tmp_path / "workspace.png"
    secret = b"configured-private-token"
    path.write_bytes(png_with_text(b"zTXt", secret))
    with pytest.raises(ValueError, match="Configured private value") as failure:
        BUILDER["validate_inputs"]([path], tmp_path, {secret})
    assert secret.decode() not in str(failure.value)


def test_public_snapshot_omits_private_helper_and_generated_inputs(tmp_path):
    inputs = [
        "README.md", ".env", "scripts/configure_local.py", "scripts/ordinary.py",
        "scripts/__pycache__/ordinary.pyc", "artifacts/README.md", "artifacts/private-run.json",
        "scripts/analysis/local_notes.py", "scripts/fc_gold_facts.py", "scripts/fc_report.py",
    ]
    for name in inputs:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private helper must not be exported", encoding="utf-8")
    selected = {p.relative_to(tmp_path).as_posix() for p in BUILDER["selected_paths"](tmp_path)}
    assert selected == {"README.md", "scripts/ordinary.py", "artifacts/README.md"}
    assert (tmp_path / "scripts/configure_local.py").is_file()


def test_public_snapshot_checks_session_and_sensitive_json_header_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        'FC_SESSION_ID=fixture-private-session\n'
        'FC_EXTRA_HEADERS={"Authorization":"Bearer fixture-private-credential","Content-Type":"application/json"}\n',
        encoding="utf-8",
    )
    monkeypatch.setitem(BUILDER["private_values"].__globals__, "ROOT", tmp_path)
    values = BUILDER["private_values"]()
    assert b"fixture-private-session" in values
    assert b"Bearer fixture-private-credential" in values
    assert b"fixture-private-credential" in values
    assert b"application/json" not in values
    file = tmp_path / "leaked.py"
    file.write_text('header = "Bearer fixture-private-credential"', encoding="utf-8")
    with pytest.raises(ValueError, match="Configured private value"):
        BUILDER["validate_inputs"]([file], tmp_path, values)


def test_public_snapshot_applies_git_eol_policy_to_new_untracked_files(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitattributes").write_text("* text=auto eol=lf\n*.py text eol=lf\n", encoding="utf-8")
    source = tmp_path / "new.py"
    source.write_bytes(b"value = 1\r\n")
    image = tmp_path / "image.png"
    image.write_bytes(b"\x00binary\r\n")
    targets = BUILDER["eol_targets"](tmp_path, [source, image])
    assert targets["new.py"] == "lf"
    assert targets["image.png"] == "raw"


@pytest.mark.parametrize("content", [
    b"E:" + b"/" + b"private-project/.env",
    b"C:" + b"\\\\" + b"users\\\\person\\\\project",
    b"/" + b"home/person/project",
    b"/" + b"Users/person/project",
])
def test_public_snapshot_rejects_machine_paths_without_echoing_private_content(tmp_path, content):
    path = tmp_path / "README.md"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="Absolute machine path") as failure:
        BUILDER["validate_inputs"]([path], tmp_path, set())
    assert str(failure.value).endswith("README.md")
    assert "person" not in str(failure.value)
    assert "private-project" not in str(failure.value)


def test_public_snapshot_allows_sources_relative_paths_and_placeholders(tmp_path):
    path = tmp_path / "README.md"
    path.write_text(
        'https://www.sec.gov/Archives/edgar .filingcopilot/ "<archive-path>" %SystemDrive%/',
        encoding="utf-8",
    )
    BUILDER["validate_inputs"]([path], tmp_path, set())


def test_public_snapshot_rejects_configured_secret_without_echoing_it(tmp_path):
    path = tmp_path / "README.md"
    path.write_bytes(b"credential contents")
    with pytest.raises(ValueError, match="Configured private value") as failure:
        BUILDER["validate_inputs"]([path], tmp_path, {b"credential contents"})
    assert "credential contents" not in str(failure.value)


@pytest.mark.slow
def test_public_snapshot_archive_excludes_git_and_retained_private_helper(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("Public source", encoding="utf-8")
    private = tmp_path / "scripts/configure_local.py"
    private.parent.mkdir()
    private.write_text("raise RuntimeError('private helper must never execute')", encoding="utf-8")
    output, archive = tmp_path / ".filingcopilot/selected", tmp_path / "dist/source.zip"
    monkeypatch.setitem(BUILDER["main"].__globals__, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["builder", "--output", str(output), "--zip", str(archive)])
    BUILDER["main"]()
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {"README.md", "release-manifest.json"}
        manifest = json.loads(zipped.read("release-manifest.json"))
        assert manifest["files"] == [
            {"path": "README.md", "sha256": hashlib.sha256(b"Public source").hexdigest()}
        ]
        assert manifest["absolute_machine_paths_checked"]
    assert private.is_file()
    assert not (output / "scripts/configure_local.py").exists()
    assert subprocess.check_output(["git", "-C", str(output), "rev-list", "--count", "HEAD"]).strip() == b"1"
    assert not subprocess.check_output(["git", "-C", str(output), "remote"])
