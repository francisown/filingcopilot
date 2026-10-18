"""Create a selected local release copy; original history/evidence stay intact."""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TREES = ("src", "tests", "scripts", "docs", "examples", "config", "evaluation", "packages", ".github")
FILES = ("README.md", "README.zh-CN.md", "CONTRIBUTING.md", "ROADMAP.md", "LICENSE", "pyproject.toml", "requirements-lock.txt", ".env.example", ".gitignore", ".gitattributes",
         ".dockerignore", "Dockerfile", "docker-compose.yml", "setup.ps1", "start.ps1")
RECORDS = ("README.md", "third-round-cold-PDD-2026-10-11.json",
           "third-round-cold-BABA-2026-10-11.json", "semantic-production-evaluation-2026-10-11.json",
           "retrieval-frozen-diagnosis-2026-10-11.json", "fourth-round-release-validation-2026-10-11.json",
           "fourth-round-preservation-final-2026-10-12.json",
           "publication-preparation-2026-10-12.json", "bilingual-validation-2026-10-12.json",
           "english-default-validation-2026-10-12.json", "native-english-publication-2026-10-12.json",
           "overnight-release-validation-2026-10-12.json",
           "overnight-installation-validation-2026-10-12.json", "overnight-window-validation-2026-10-12.json",
           "overnight-plan-language-validation-2026-10-13.json", "overnight-api-error-language-validation-2026-10-13.json")
PRIVATE_INPUTS = frozenset({
    "scripts/configure_local.py", "scripts/fc_gold_facts.py", "scripts/fc_report.py",
})
PRIVATE_TREES = ("scripts/analysis/",)


def local_exclusions() -> frozenset[str]:
    """Optional local list of paths excluded from public snapshots.

    The list lives under the ignored build home so internal document names
    are not published with the builder itself.
    """
    config = ROOT / ".filingcopilot" / "public-exclusions.json"
    try:
        entries = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    cleaned = (str(entry).replace("\\", "/").strip().lstrip("/") for entry in entries)
    return frozenset(entry for entry in cleaned if entry)
MACHINE_PATH = re.compile(
    rb"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+|/(?:users|home)/[^/\s\"'<>]+/", re.IGNORECASE
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def scan_content(content: bytes) -> bytes:
    """Inspect text and PNG metadata without treating compressed pixels as path strings."""
    if not content.startswith(PNG_SIGNATURE):
        return content
    inspected, offset, first, pixels = [], len(PNG_SIGNATURE), True, False

    def inflate(value):
        decoder = zlib.decompressobj()
        output = decoder.decompress(value, 1_000_001)
        if len(output) > 1_000_000 or not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid or oversized PNG metadata")
        return output

    while offset < len(content):
        if offset + 12 > len(content):
            raise ValueError("Invalid PNG structure")
        length = struct.unpack_from(">I", content, offset)[0]
        end = offset + 12 + length
        if end > len(content):
            raise ValueError("Invalid PNG structure")
        kind, value = content[offset + 4:offset + 8], content[offset + 8:end - 4]
        checksum = struct.unpack_from(">I", content, end - 4)[0]
        if checksum != zlib.crc32(kind + value) or (first and (kind != b"IHDR" or length != 13)):
            raise ValueError("Invalid PNG structure")
        first = False
        if kind == b"IDAT":
            pixels = True
        else:
            inspected.append(value)
            if kind in {b"zTXt", b"iCCP"}:
                _, sep, body = value.partition(b"\0")
                if not sep or not body or body[0] != 0:
                    raise ValueError("Invalid PNG metadata")
                inspected.append(inflate(body[1:]))
            elif kind == b"iTXt":
                _, sep, body = value.partition(b"\0")
                if not sep or len(body) < 2 or body[0] not in (0, 1) or body[1] != 0:
                    raise ValueError("Invalid PNG metadata")
                text = body[2:].split(b"\0", 2)
                if len(text) != 3:
                    raise ValueError("Invalid PNG metadata")
                inspected.append(inflate(text[2]) if body[0] else text[2])
        offset = end
        if kind == b"IEND":
            if length or offset != len(content) or not pixels:
                raise ValueError("Invalid PNG structure")
            return b"\n".join(inspected)
    raise ValueError("Invalid PNG structure")


def selected_paths(root: Path) -> list[Path]:
    paths = [root / name for name in FILES if (root / name).is_file()]
    for tree in TREES:
        paths += [p for p in (root / tree).rglob("*") if p.is_file()
                  and not any(part in {"__pycache__", "node_modules", ".venv", "build", "dist"}
                              or part.endswith(".egg-info") for part in p.parts)
                  and p.suffix not in {".pyc", ".pyo"}]
    paths += [root / "artifacts" / name for name in RECORDS
              if (root / "artifacts" / name).is_file()]
    excluded = PRIVATE_INPUTS | local_exclusions()
    return sorted({p for p in paths if p.relative_to(root).as_posix() not in excluded
                   and not p.relative_to(root).as_posix().startswith(PRIVATE_TREES)})


def eol_targets(root: Path, paths: list[Path]) -> dict[str, str]:
    """Resolve the line-ending policy git applies on a fresh checkout.

    The manifest must hash the bytes that a clone of the published repository
    (and the published zip) actually contains, not whichever mix of CRLF/LF
    the build machine's worktree happens to have. Files git treats as binary
    or frozen (-text) stay raw; otherwise the declared eol attribute wins.
    """
    targets = {}
    rels = [p.relative_to(root).as_posix() for p in paths]
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                               "--exclude-standard", "--eol", "-z", "--", *rels],
                              capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return targets
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        header, _, name = entry.partition(b"\t")
        if not name:
            continue
        text = header.decode("utf-8", "replace")
        index_eol = text.split()[0] if text.split() else ""
        attr = text.split("attr/", 1)[1] if "attr/" in text else ""
        if index_eol == "i/-text" or "w/-text" in text.split() or attr.strip() == "-text":
            targets[name.decode("utf-8")] = "raw"
        elif "eol=crlf" in attr:
            targets[name.decode("utf-8")] = "crlf"
        elif "eol=lf" in attr:
            targets[name.decode("utf-8")] = "lf"
    return targets


def validate_inputs(paths: list[Path], root: Path, values: set[bytes]) -> None:
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Source symlink/escaped path is not a release input")
        content = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        inspected = scan_content(content)
        if any(value in content or value in inspected for value in values):
            raise ValueError("Configured private value found in selected file: " + relative)
        if MACHINE_PATH.search(inspected):
            raise ValueError("Absolute machine path found in selected file: " + relative)


def private_values():
    values = set()
    for key, value in dotenv_values(ROOT / ".env").items():
        if value and len(value) >= 6 and any(
            part in key.upper() for part in ("KEY", "TOKEN", "PASSWORD", "CONTACT", "EMAIL", "USER_AGENT", "SESSION", "SECRET")
        ):
            values.add(value.encode())
            values.update(email.encode() for email in re.findall(r"[\w.+-]+@[\w.-]+", value))
        if key == "FC_EXTRA_HEADERS" and value:
            values.add(value.encode())
            try:
                headers = json.loads(value)
            except ValueError:
                continue
            if isinstance(headers, dict):
                for name, header in headers.items():
                    if isinstance(header, str) and len(header) >= 6 and re.search(
                        r"authorization|cookie|token|key|secret|session", name, re.I,
                    ):
                        values.add(header.encode())
                        if header.lower().startswith(("bearer ", "basic ")):
                            values.add(header.split(" ", 1)[1].encode())
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()
    output, archive = args.output.resolve(), args.zip.resolve()
    if not output.is_relative_to(ROOT / ".filingcopilot") or not archive.is_relative_to(ROOT / "dist"):
        raise ValueError("Release copies must be new local managed paths, not source/history paths")
    if output.exists() or archive.exists():
        raise ValueError("Output already exists; preserve previous release copies")
    paths = selected_paths(ROOT)
    selected = {p.relative_to(ROOT).as_posix() for p in paths}
    values = private_values()
    validate_inputs(paths, ROOT, values)
    endings = eol_targets(ROOT, paths)
    output.mkdir(parents=True)
    hashes, redirected = {}, []
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = path.read_bytes()
        declared = endings.get(relative.as_posix())
        if declared == "crlf":
            content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        elif declared == "lf":
            content = content.replace(b"\r\n", b"\n")
        if path.suffix == ".md":
            text = content.decode("utf-8")

            def link(match):
                href = match.group(3)
                if href.startswith(("http:", "https:", "mailto:", "#")):
                    return match.group(0)
                candidate = (path.parent / href.split("#", 1)[0]).resolve()
                if candidate.is_relative_to(ROOT / "artifacts") and candidate.relative_to(ROOT).as_posix() not in selected:
                    destination = os.path.relpath(output / "artifacts/README.md", target.parent).replace("\\", "/")
                    redirected.append({"document": relative.as_posix(), "omitted": href})
                    return "[" + match.group(2) + "](" + destination + ")"
                return match.group(0)

            content = re.sub(r"(!?)\[([^\]]+)\]\(([^)]+)\)", link, text).encode("utf-8")
        target.write_bytes(content)
        hashes[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    manifest = {"schema": "filingcopilot.public-snapshot/v2",
                "files": [{"path": name, "sha256": sha} for name, sha in hashes.items()],
                "artifacts_files": sum(name.startswith("artifacts/") for name in hashes),
                "omitted_evidence_links": redirected, "configured_private_values_checked": bool(values),
                "original_repository_preserved": True, "remote_publication": False,
                "absolute_machine_paths_checked": True,
                "private_helpers_excluded": sorted(PRIVATE_INPUTS),
                "private_trees_excluded": list(PRIVATE_TREES),
                "security_scope": "Selected files and decoded PNG metadata checked for configured credentials/contact and machine paths; visually review images and run gitleaks separately."}
    (output / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(output)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(output), "add", "-f", "--", *hashes, "release-manifest.json"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(output), "-c", "user.name=FilingCopilot build bot",
                    "-c", "user.email=filingcopilot-bot@localhost", "commit", "-m", "FilingCopilot v1.0.0"],
                   check=True, capture_output=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name in [*hashes, "release-manifest.json"]:
            zipped.write(output / name, name)
    print(json.dumps({"output": str(output), "zip": str(archive), "files": len(hashes),
                      "artifact_files": manifest["artifacts_files"], "zip_bytes": archive.stat().st_size,
                      "remote_publication": False}))


if __name__ == "__main__":
    main()
