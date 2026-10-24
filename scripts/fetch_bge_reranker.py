"""Download and verify the BGE reranker used by the full-filing evaluator.

The model is intentionally not committed. This script resumes interrupted
downloads and verifies the ONNX file's size and SHA256 before use.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import httpx

REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
REVISION = "main"
MODEL = "model_int8.onnx"
MODEL_SIZE = 570_727_094
MODEL_SHA256 = "912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d"
FILES = {
    "config.json": 848,
    "special_tokens_map.json": 964,
    "tokenizer_config.json": 1203,
    "tokenizer.json": 17_082_900,
    "onnx/model_int8.onnx": MODEL_SIZE,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(client: httpx.Client, url: str, target: Path, expected: int) -> None:
    if target.exists() and target.stat().st_size == expected:
        return
    part = target.with_suffix(target.suffix + ".part")
    if part.exists() and part.stat().st_size > expected:
        part.unlink()
    for attempt in range(1, 9):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416 and offset == expected:
                    part.replace(target)
                    return
                response.raise_for_status()
                mode = "ab" if offset and response.status_code == 206 else "wb"
                with part.open(mode) as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        handle.write(chunk)
            if part.stat().st_size == expected:
                part.replace(target)
                return
            raise RuntimeError(
                f"size mismatch: {part.stat().st_size} != {expected}"
            )
        except Exception as exc:  # network and partial-read failures
            print(f"retry {attempt}/8 {target.name}: {type(exc).__name__}", flush=True)
            time.sleep(min(30, 2 * attempt))
    raise RuntimeError(f"download failed: {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=f"https://huggingface.co/{REPO}/resolve/{REVISION}",
        help="HF or mirror resolve base URL",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("models/bge-reranker-v2-m3-int8"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60, connect=30),
        headers={"User-Agent": "filingcopilot-repro/1.0"},
    ) as client:
        for name, expected in FILES.items():
            target = args.output_dir / Path(name).name
            if args.force:
                target.unlink(missing_ok=True)
                target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
            print(f"fetch {name} -> {target}", flush=True)
            download(client, f"{args.base_url.rstrip('/')}/{name}", target, expected)
    model = args.output_dir / MODEL
    actual = sha256(model)
    if actual != MODEL_SHA256:
        raise SystemExit(f"SHA256 mismatch: {actual} != {MODEL_SHA256}")
    print(f"verified {model} ({model.stat().st_size} bytes, {actual})")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
