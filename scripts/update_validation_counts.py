"""Keep the published regression-test count in sync across docs.

Usage:
    python scripts/update_validation_counts.py 2336 --check
    python scripts/update_validation_counts.py 2336 --write

The count is intentionally a separate input: pytest's final number depends on
installed extras and skips, so the docs should be updated only after the same
command that produced the published suite has finished.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    ROOT / "README.md": re.compile(r"(\| Regression tests \| \*\*)([0-9][0-9,]*)( passed\*\* \|)"),
    ROOT / "README.zh-CN.md": re.compile(r"(\| 回归测试 \| \*\*)([0-9][0-9,]*)( passed\*\* \|)"),
    ROOT / "docs" / "evaluation.md": re.compile(
        r"(\| (?:Regression suite|Current regression suite) \| \*\*)([0-9][0-9,]*)( passed\*\* \|)"
    ),
    ROOT / "docs" / "case-study.md": re.compile(r"(\*\*)([0-9][0-9,]*)( tests\*\*)"),
    ROOT / "docs" / "case-study.zh-CN.md": re.compile(r"(\*\*)([0-9][0-9,]*)( 个测试\*\*)"),
}


def _replace(pattern: re.Pattern[str], text: str, count: str) -> tuple[str, list[str]]:
    found: list[str] = []

    def repl(match: re.Match[str]) -> str:
        found.append(match.group(2))
        return match.group(1) + count + match.group(3)

    return pattern.sub(repl, text), found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("count", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="update the documents")
    mode.add_argument("--check", action="store_true", help="verify only (default)")
    args = parser.parse_args()
    count = f"{args.count:,}"
    mismatches: list[str] = []
    for path, pattern in TARGETS.items():
        text = path.read_text(encoding="utf-8")
        updated, found = _replace(pattern, text, count)
        if not found:
            mismatches.append(f"{path.relative_to(ROOT)}: no published count found")
            continue
        if any(value != count for value in found):
            mismatches.append(
                f"{path.relative_to(ROOT)}: {', '.join(sorted(set(found)))} != {count}"
            )
            if args.write:
                path.write_text(updated, encoding="utf-8")
    if mismatches:
        print("\n".join(mismatches))
        if not args.write:
            return 1
    print(f"validation count: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
