"""Validate the exact local model snapshot used by optional retrieval trials."""

import hashlib
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_model_snapshot(folder: Path, model_name: str) -> dict:
    root = folder.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("model_name") != model_name:
        raise ValueError("Local model manifest does not match the requested model")
    hashes = manifest.get("sha256", {})
    if not isinstance(hashes, dict) or not hashes or not manifest.get("revision"):
        raise ValueError("Local model snapshot lacks revision or file hashes")
    for name, expected in hashes.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Local model file escapes its snapshot directory")
        if file_sha256(path) != expected:
            raise ValueError("Local model file hash does not match its snapshot")
    return manifest
