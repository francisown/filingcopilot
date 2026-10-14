"""Read-only offline CLI. No service, model, configuration or network startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .audit import Package, audit_package
from .verification_badge import consistency_badge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument("--schema", action="store_true", help="Print the versioned package JSON Schema")
    parser.add_argument("--source-cache", type=Path, help="Caller-trusted SEC/localized filing byte cache")
    parser.add_argument("--corpus-home", type=Path, help="Caller-trusted existing local corpus; never creates a new corpus")
    parser.add_argument("--domain-config", type=Path, help="Caller-trusted domain configuration for source comparison")
    parser.add_argument("--market-config", type=Path, help="Caller-trusted listing/alias registry")
    parser.add_argument("--market-cache", type=Path, help="Caller-trusted market JSON byte cache")
    parser.add_argument("--external-cache", type=Path, help="Caller-trusted external disclosure byte cache")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--badge", type=Path, help="Write a local SVG of exact checking scope and package file checksum")
    args = parser.parse_args(argv)
    if not args.schema and args.package is None:
        parser.error("supply a package path or --schema")
    if args.schema and args.badge:
        parser.error('--badge requires a package, not --schema')
    try:
        if args.schema:
            report, code = Package.model_json_schema(), 0
        else:
            package_bytes = args.package.read_bytes()
            payload = json.loads(package_bytes)
            corpus, trusted = None, None
            if args.corpus_home or args.domain_config:
                from .corpus import Corpus
                from .domain import Domain, load_domain
                from .stock_directory import StockDirectory, directory_enriched_companies

                if args.corpus_home:
                    if not (args.corpus_home / 'corpus.sqlite3').is_file():
                        raise ValueError('Existing caller-trusted corpus is required')
                    corpus = Corpus(args.corpus_home, readonly=True)
                trusted = load_domain(args.domain_config, corpus.registered_companies() if corpus else None)
                trusted = Domain.model_validate({**trusted.model_dump(), 'companies': directory_enriched_companies(
                    trusted.model_dump()['companies'], StockDirectory(args.market_config))})
            report = audit_package(payload, corpus=corpus, trusted_domain=trusted, source_cache=args.source_cache,
                                   market_cache=args.market_cache, external_cache=args.external_cache)
            code = 0 if report["status"] == "consistent" else 1
            if args.badge:
                args.badge.parent.mkdir(parents=True, exist_ok=True)
                args.badge.write_text(consistency_badge(report, hashlib.sha256(package_bytes).hexdigest()), encoding='utf-8')
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return code
    except (OSError, ValueError, TypeError) as exc:
        # Avoid echoing untrusted package contents or environment values.
        sys.stderr.write(f"Unable to read or verify package: {type(exc).__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
