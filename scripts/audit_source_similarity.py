"""Heuristic local comparison of checked-out public sources; no remote code execution.

This detects exact files and sufficiently long verbatim sequences, not authorship,
legal infringement, renamed/rewritten copying or semantic similarity.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import tokenize
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".html", ".css", ".md"}
TOKEN_WINDOW = 40
LINE_WINDOW = 6


def tokens(text: str, suffix: str) -> list[str]:
    if suffix == ".py":
        excluded = {
            tokenize.COMMENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.ENCODING,
        }
        try:
            result = []
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type in excluded:
                    continue
                value = token.string
                if token.type == tokenize.STRING:
                    try:
                        value = repr(ast.literal_eval(value))
                    except (ValueError, SyntaxError):
                        pass
                result.append(value)
            return result
        except (tokenize.TokenError, IndentationError):
            pass
    return re.findall(r"\w+|[^\w\s]", text)


def meaningful_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\w", line) and not line.lstrip().startswith(("#", "//", "<!--"))
    ]


def windows(values: list[str], size: int):
    for index in range(len(values) - size + 1):
        yield tuple(values[index : index + size])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path, help="JSON list containing repo, sha, path")
    parser.add_argument("--revision", default="HEAD", help="Local Git commit to compare")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/source-audit-2026-10-08.json")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{args.revision}^{{commit}}"], cwd=root, text=True
    ).strip()
    names = (
        subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", revision], cwd=root)
        .decode("utf-8")
        .split("\0")
    )
    local_files = [
        name
        for name in names
        if name
        and Path(name).suffix in EXTENSIONS
        and not name.startswith(("artifacts/", "src/filingcopilot/samples/"))
    ]
    local = {}
    for name in local_files:
        raw = subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=root)
        text = raw.decode("utf-8-sig", errors="replace")
        local[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "tokens": tokens(text, Path(name).suffix),
            "lines": meaningful_lines(text),
        }
    token_index, line_index, hash_index = defaultdict(set), defaultdict(set), defaultdict(set)
    for name, data in local.items():
        hash_index[data["sha256"]].add(name)
        for gram in windows(data["tokens"], TOKEN_WINDOW):
            token_index[gram].add(name)
        for gram in windows(data["lines"], LINE_WINDOW):
            if sum(map(len, gram)) >= 200:
                line_index[gram].add(name)
    report = {
        "date": "2026-10-08",
        "local_commit": revision,
        "local_file_count": len(local),
        "local_files": list(local),
        "method": {
            "token_window": TOKEN_WINDOW,
            "meaningful_line_window": LINE_WINDOW,
            "minimum_line_block_characters": 200,
            "excluded": "source snapshots, run artifacts, installed dependencies; blank/comment-only lines",
            "scope": "application, scripts, tests, UI, README and supplied design document",
            "limits": "Heuristic screening only. No renamed-code detection or semantic clone detection; "
            "zero long matches does not prove absence of copying. No plagiarism percentage.",
        },
        "repositories": [],
    }
    for entry in json.loads(args.metadata.read_text(encoding="utf-8")):
        remote_root = Path(entry["path"])
        files = [
            path
            for path in remote_root.rglob("*")
            if path.is_file() and path.suffix in EXTENSIONS and path.stat().st_size < 2_000_000
        ]
        exact, candidates, line_matches, sequences = [], [], [], []
        for path in files:
            name = path.relative_to(remote_root).as_posix()
            for ours in hash_index.get(hashlib.sha256(path.read_bytes()).hexdigest(), ()):
                exact.append({"local": ours, "remote": name})
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            sequence = tokens(text, path.suffix)
            counts = defaultdict(int)
            for gram in windows(sequence, TOKEN_WINDOW):
                for ours in token_index.get(gram, ()):
                    counts[ours] += 1
            for ours, shared in counts.items():
                candidates.append((shared, ours, name, sequence))
            matched_local = set()
            for gram in windows(meaningful_lines(text), LINE_WINDOW):
                matched_local.update(line_index.get(gram, ()))
            for ours in matched_local:
                line_matches.append({"local": ours, "remote": name})
        for _, ours, name, sequence in sorted(candidates, key=lambda row: row[0], reverse=True)[
            :20
        ]:
            match = SequenceMatcher(
                None, local[ours]["tokens"], sequence, autojunk=False
            ).find_longest_match()
            sequences.append(
                {
                    "local": ours,
                    "remote": name,
                    "tokens": match.size,
                    "local_token_start": match.a,
                    "remote_token_start": match.b,
                }
            )
        result = {
            "repo": entry["repo"],
            "sha": entry["sha"],
            "url": f"https://github.com/{entry['repo']}/tree/{entry['sha']}",
            "archive_url": f"https://codeload.github.com/{entry['repo']}/zip/{entry['sha']}",
            "created_at": entry.get("created_at"),
            "commit_date": entry.get("commit_date"),
            "reported_license": entry.get("license"),
            "scanned_files": len(files),
            "scanned_code_files": sum(path.suffix != ".md" for path in files),
            "scanned_markdown_files": sum(path.suffix == ".md" for path in files),
            "byte_identical_files": exact,
            "long_line_matches": line_matches,
            "long_token_matches": sorted(sequences, key=lambda row: row["tokens"], reverse=True),
        }
        report["repositories"].append(result)
        print(
            json.dumps(
                {
                    "repo": entry["repo"],
                    "scanned_files": len(files),
                    "identical": len(exact),
                    "long_lines": len(line_matches),
                    "long_tokens": len(sequences),
                }
            ),
            flush=True,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
