"""Download a pinned official Qdrant BGE export using verified HTTPS, including on Windows.

Some proxy stacks fail Hugging Face's SDK TLS path. urllib direct access is a second transport;
neither path disables certificate verification. No third-party model mirrors are used.
"""

import hashlib
import json
import urllib.request

from filingcopilot.settings import Settings

settings = Settings.from_env()
repo = "Qdrant/bge-small-en-v1.5-onnx-Q"
revision = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
folder = settings.home / "models" / "bge-local"
folder.mkdir(parents=True, exist_ok=True)
manifest_path = folder / "manifest.json"
prior = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
names = [
    "config.json",
    "model_optimized.onnx",
    "ort_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
]
hashes = {}
for name in names:
    path = folder / name
    valid = (
        path.exists()
        and prior.get("revision") == revision
        and hashlib.sha256(path.read_bytes()).hexdigest() == prior.get("sha256", {}).get(name)
    )
    if not valid:
        print("Downloading", name, flush=True)
        url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
        with (
            opener.open(url, timeout=120) as source,
            path.with_suffix(path.suffix + ".tmp").open("wb") as dest,
        ):
            while block := source.read(1024 * 1024):
                dest.write(block)
        path.with_suffix(path.suffix + ".tmp").replace(path)
    hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
(folder / "manifest.json").write_text(
    json.dumps({"repo": repo, "revision": revision, "sha256": hashes}, indent=2), encoding="utf-8"
)
print("Official model ready at", folder, flush=True)
