"""Refresh UI cache keys after editing shipped assets (not a security digest)."""

import hashlib
import re
from pathlib import Path

from filingcopilot import __version__

folder = Path(__file__).resolve().parents[1] / "src" / "filingcopilot" / "static"
index = folder / "index.html"


def replace(match):
    name = match[1]
    # Match the portable browser gate: Windows CRLF and Git LF have the same key.
    source = (folder / name).read_text(encoding="utf-8").encode("utf-8")
    digest = hashlib.sha256(source).hexdigest()[:10]
    return f"/static/{name}?v={__version__}-{digest}"


source = index.read_text(encoding="utf-8")
updated = re.sub(r'/static/([a-z-]+\.(?:js|css))\?v=[^"\s]+', replace, source)
if updated != source:
    index.write_text(updated, encoding="utf-8")
print("UI cache keys match the shipped asset bytes.")
