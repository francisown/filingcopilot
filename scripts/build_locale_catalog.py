"""Build the browser catalogue from the shared offline localization resource."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/filingcopilot"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = json.loads((ROOT / "locale-en.json").read_text(encoding="utf-8"))
    content = (
        "// Generated from locale-en.json. Run scripts/build_locale_catalog.py after editing the catalogue.\n"
        + "globalThis.FilingLocaleEnglish = "
        + json.dumps(source, ensure_ascii=False, indent=2)
        + ";\n"
    )
    target = ROOT / "static/locale-en.js"
    if args.check:
        if target.read_text(encoding="utf-8") != content:
            raise ValueError("Browser locale catalogue differs from shared resource")
    else:
        target.write_text(content, encoding="utf-8")
    print(f"Locale catalogue: {len(source)} entries")


if __name__ == "__main__":
    main()
