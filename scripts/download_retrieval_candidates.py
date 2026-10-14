"""Download pinned first-party model snapshots without disabling verified HTTPS.

Downloaded-file hashes support repeatability and corruption checks, not an
independent signature. Sources and revisions are explicit and version controlled.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from filingcopilot.local_models import file_sha256, verify_model_snapshot

MODELS = {
    "cross-encoder": {
        "repo": "Xenova/ms-marco-MiniLM-L-6-v2",
        "model_name": "Xenova/ms-marco-MiniLM-L-6-v2",
        "revision": "a09144355adeed5f58c8ed011d209bf8ee5a1fec",
        "files": ["config.json", "special_tokens_map.json", "tokenizer.json",
                  "tokenizer_config.json", "vocab.txt", "onnx/model.onnx"],
    },
    "multilingual": {
        "repo": "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
        "model_name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "revision": "faf4aa4225822f3bc6376869cb1164e8e3feedd0",
        "files": ["config.json", "ort_config.json", "special_tokens_map.json",
                  "tokenizer.json", "tokenizer_config.json", "unigram.json", "model_optimized.onnx"],
    },
}


def download_snapshot(spec: dict, folder: Path, opener, timeout: float = 30) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    prior = {}
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = {}
    for name in spec["files"]:
        path = folder / name
        expected = prior.get("sha256", {}).get(name)
        if (prior.get("revision") == spec["revision"]
                and prior.get("model_name") == spec["model_name"] and path.is_file()
                and file_sha256(path) == expected):
            hashes[name] = expected
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".download")
        url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}"
        print(f"Downloading {name}", flush=True)
        with opener.open(url, timeout=timeout) as source, temp.open("wb") as dest:
            while block := source.read(1024 * 1024):
                dest.write(block)
        temp.replace(path)
        hashes[name] = file_sha256(path)
    manifest = {k: spec[k] for k in ("repo", "model_name", "revision")}
    manifest.update(sha256=hashes, transport="verified HTTPS from publisher repository")
    temp_manifest = manifest_path.with_suffix(".tmp")
    temp_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temp_manifest.replace(manifest_path)
    verify_model_snapshot(folder, spec["model_name"])
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=MODELS)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--direct", action="store_true", help="Use direct HTTPS instead of the configured proxy")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
              if args.direct else urllib.request.build_opener())
    try:
        manifest = download_snapshot(MODELS[args.model], args.destination, opener, args.timeout)
    except Exception as exc:
        # Keep credential-bearing proxy and signed redirect URLs out of logs.
        print(json.dumps({"status": "download_failed", "error_type": type(exc).__name__}))
        raise SystemExit(2) from None
    print(json.dumps({"status": "ready", "revision": manifest["revision"], "files": len(manifest["sha256"])}))


if __name__ == "__main__":
    main()
