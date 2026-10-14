from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from .comparison import ComparisonRequest
from .models import ResearchRequest
from .service import FilingCopilot, directory_enriched_companies
from .settings import Settings
from .stock_directory import StockDirectory
from .trends import TrendRequest


def main():
    parser = argparse.ArgumentParser(prog="filingcopilot")
    parser.add_argument("--home", help="Absolute evidence database and cache directory")
    commands = parser.add_subparsers(dest="command", required=True)
    watch = commands.add_parser("watchlist", help="Local saved listing preferences")
    watch.add_argument("action", choices=["list", "add", "remove"], default="list", nargs="?")
    watch.add_argument("ticker", nargs="?")
    web = commands.add_parser("serve")
    web.add_argument("--port", type=int, default=8159)
    web.add_argument("--host", default="127.0.0.1")
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    mcp.add_argument("--port", type=int, default=8124)
    ask = commands.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--ticker")
    ask.add_argument(
        "--company",
        help="Find a company by English name/ticker; Agent mode can translate Chinese names",
    )
    ask.add_argument(
        "--auto-acquire",
        action="store_true",
        help="Prepare missing SEC annual filings before research",
    )
    ask.add_argument("--as-of", default=date.today().isoformat())
    ask.add_argument("--mode", choices=["agent", "reference"], default="agent")
    ask.add_argument("--full", action="store_true", help="Six-section complete company research")
    ask.add_argument("--output")
    ask.add_argument("--package", action="store_true", help="Export a self-contained audit package")
    audit = commands.add_parser("audit", help="Recheck a package without model or network calls")
    audit.add_argument("path", type=Path)
    audit.add_argument("--against-corpus", action="store_true")
    audit.add_argument("--source-cache", type=Path)
    audit.add_argument("--market-cache", type=Path)
    audit.add_argument("--external-cache", type=Path)
    audit.add_argument("--output", type=Path)
    verify_package = commands.add_parser(
        "verify-package", help="Standalone offline verifier for an exported research package"
    )
    verify_package.add_argument("path", type=Path)
    verify_package.add_argument("--against-corpus", action="store_true")
    verify_package.add_argument("--source-cache", type=Path)
    verify_package.add_argument("--market-cache", type=Path)
    verify_package.add_argument("--external-cache", type=Path)
    verify_package.add_argument("--output", type=Path)
    attribution = commands.add_parser(
        "attribution", help="Compare two frozen research packages and explain version changes"
    )
    attribution.add_argument("before", type=Path)
    attribution.add_argument("after", type=Path)
    attribution.add_argument("--output", type=Path)
    export = commands.add_parser("export", help="Export an existing saved research package")
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True)
    versions = commands.add_parser(
        "versions", help="Compare annual disclosure versions before as-of"
    )
    versions.add_argument("ticker", type=str.upper)
    versions.add_argument("metric")
    versions.add_argument("fiscal_year", type=int)
    versions.add_argument("--as-of", default=date.today().isoformat())
    commands.add_parser("index")
    commands.add_parser("catalog")
    find = commands.add_parser("find-company", help="Search the official SEC company directory")
    find.add_argument("query")
    find.add_argument("--no-translate", action="store_true")
    stock_find = commands.add_parser("find-stock", help="Find a market symbol or SEC company")
    stock_find.add_argument("query")
    stock_find.add_argument("--no-translate", action="store_true")
    market = commands.add_parser("market", help="Read long-term price series without a model")
    market.add_argument("ticker")
    market.add_argument(
        "--period", choices=["1M", "6M", "1Y", "3Y", "5Y", "10Y", "MAX"], default="5Y"
    )
    market.add_argument("--as-of", default=date.today().isoformat())
    historical = commands.add_parser(
        "historical-valuation", help="Rebuild source-bound historical annual P/E references"
    )
    historical.add_argument("ticker")
    historical.add_argument("--period", choices=["1Y", "5Y", "MAX"], default="5Y")
    historical.add_argument("--as-of", default=date.today().isoformat())
    trend = commands.add_parser(
        "trend", help="Source-bound three/five-year annual financial trends"
    )
    trend.add_argument("ticker")
    trend.add_argument("--years", type=int, choices=[3, 5], default=5)
    trend.add_argument("--as-of", default=date.today().isoformat())
    trend.add_argument("--local-sources", action="store_true")
    trend.add_argument("--output", type=Path)
    trend.add_argument("--package", action="store_true")
    compare = commands.add_parser(
        "compare", help="Compare 2-4 listings, or 1-4 with an ETF benchmark"
    )
    compare.add_argument("tickers", nargs="+")
    compare.add_argument("--period", choices=["1Y", "5Y", "MAX"], default="5Y")
    compare.add_argument("--benchmark", choices=["SPY", "QQQ"])
    compare.add_argument(
        "--ttm-valuation", action="store_true", help="Add current supplier EPS/P-E"
    )
    compare.add_argument("--as-of", default=date.today().isoformat())
    compare.add_argument(
        "--local-sources", action="store_true", help="Skip online annual source checks"
    )
    compare.add_argument("--output", type=Path)
    compare.add_argument("--package", action="store_true")
    prepare = commands.add_parser(
        "prepare", help="Automatically acquire and index a company's annual filings"
    )
    prepare.add_argument("ticker", type=str.upper)
    prepare.add_argument("--as-of", default=date.today().isoformat())
    prepare.add_argument("--years", type=int, choices=[1, 2, 3], default=2)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("ticker", type=str.upper, help="Ticker registered in FC_DOMAIN_CONFIG")
    ingest.add_argument("--years", type=int, choices=[1, 2, 3], default=2)
    ingest.add_argument("--offline", action="store_true")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--output")
    evaluation.add_argument("--suite", choices=["snapshot", "integrity", "all"], default="all")
    scenario = commands.add_parser(
        "scenarios", help="Cited EPS/PE scenarios with explicit assumptions"
    )
    scenario.add_argument("ticker")
    scenario.add_argument(
        "--cases", required=True, help="JSON list of name, growth_pct, terminal_pe"
    )
    scenario.add_argument("--discount", required=True)
    scenario.add_argument("--years", type=int, default=3)
    scenario.add_argument("--basis", choices=["annual", "provider_ttm"], default="annual")
    scenario.add_argument("--as-of", default=date.today().isoformat())
    scenario.add_argument("--output", type=Path)
    scenario.add_argument("--package", action="store_true")
    for command in (ask, trend, compare, scenario):
        command.add_argument("--language", choices=["en", "zh"], default="en", help="Language of new research (default: en; use zh for Chinese)")
    args = parser.parse_args()
    if args.home:
        os.environ["FC_HOME"] = str(Path(args.home).resolve())
    if args.command in ("audit", "verify-package"):
        from .audit import audit_package
        from .corpus import Corpus
        from .domain import load_domain

        corpus, trusted_domain, cache = None, None, args.source_cache
        market_cache = args.market_cache
        external_cache = args.external_cache
        if args.against_corpus:
            audit_settings = Settings.from_env()
            corpus = Corpus(audit_settings.home)
            base_domain = load_domain(audit_settings.domain_config)
            companies = directory_enriched_companies(
                {**corpus.registered_companies(), **base_domain.model_dump()["companies"]},
                StockDirectory(audit_settings.market_config),
            )
            trusted_domain = load_domain(audit_settings.domain_config, companies)
            cache = cache or audit_settings.home / "sec-cache"
            market_cache = market_cache or audit_settings.home / "market-cache"
            external_cache = external_cache or audit_settings.home / "external-cache"
        try:
            payload = json.loads(args.path.read_text(encoding="utf-8-sig"))
            report = audit_package(
                payload, corpus=corpus, trusted_domain=trusted_domain,
                source_cache=cache, market_cache=market_cache, external_cache=external_cache,
            )
        except (OSError, ValueError) as exc:
            report = {"status": "invalid", "error": str(exc)}
        content = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        print(content)
        if report["status"] != "consistent":
            raise SystemExit(1)
        return
    if args.command == "attribution":
        from .attribution import build_attribution

        try:
            report = build_attribution(
                json.loads(args.before.read_text(encoding="utf-8-sig")),
                json.loads(args.after.read_text(encoding="utf-8-sig")),
            )
        except (OSError, ValueError) as exc:
            report = {"status": "invalid", "error": str(exc)}
        content = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        print(content)
        if report.get("status") == "invalid":
            raise SystemExit(1)
        return
    settings = Settings.from_env()
    service = FilingCopilot(settings)
    if args.command == "watchlist":
        if args.action != "list" and not args.ticker:
            parser.error("watchlist add/remove requires a ticker")
        result = (
            service.watchlist.items()
            if args.action == "list"
            else getattr(service.watchlist, args.action)(args.ticker)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "scenarios":
        from .scenarios import ScenarioRequest

        result = service.valuation_scenarios(
            ScenarioRequest(
                ticker=args.ticker,
                as_of=args.as_of,
                basis=args.basis,
                years=args.years,
                discount_pct=args.discount,
                cases=json.loads(args.cases),
                language=args.language,
            )
        )
        data = service.export_package(result.id) if args.package else result.model_dump(mode="json")
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(content, "utf-8")
        else:
            print(content)
        return
    if args.command == "trend":
        result = service.financial_trend(
            TrendRequest(
                ticker=args.ticker,
                years=args.years,
                as_of=date.fromisoformat(args.as_of),
                refresh_sources=not args.local_sources,
                language=args.language,
            )
        )
        content = json.dumps(
            service.export_package(result.id) if args.package else result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content)
        return
    if args.command == "compare":
        result = service.compare_companies(
            ComparisonRequest(
                tickers=args.tickers,
                benchmark=args.benchmark,
                period=args.period,
                as_of=date.fromisoformat(args.as_of),
                refresh_sources=not args.local_sources,
                include_ttm_valuation=args.ttm_valuation,
                language=args.language,
            )
        )
        data = service.export_package(result.id) if args.package else result.model_dump(mode="json")
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content)
        return
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(service), host=args.host, port=args.port)
    elif args.command == "mcp":
        from .mcp_server import create_mcp

        create_mcp(service, args.port).run(transport=args.transport)
    elif args.command == "ask":
        result = service.research(
            ResearchRequest(
                question=args.question,
                ticker=args.ticker,
                as_of=date.fromisoformat(args.as_of),
                mode=args.mode,
                company=args.company,
                auto_acquire=args.auto_acquire,
                depth="full" if args.full else "brief",
                language=args.language,
            )
        )
        content = (
            json.dumps(service.export_package(result.id), ensure_ascii=False, indent=2)
            if args.package
            else result.model_dump_json(indent=2)
        )
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(content, encoding="utf-8")
        print(content)
    elif args.command == "export":
        content = json.dumps(service.export_package(args.run_id), ensure_ascii=False, indent=2)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(content)
    elif args.command == "versions":
        print(
            json.dumps(
                service.tools(date.fromisoformat(args.as_of)).get_fact_versions(
                    args.ticker, args.metric, args.fiscal_year
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "catalog":
        print(json.dumps(service.corpus.catalog(date.today()), ensure_ascii=False, indent=2))
    elif args.command == "find-company":
        print(
            json.dumps(
                service.discovery.search(args.query, not args.no_translate),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "find-stock":
        print(
            json.dumps(
                service.find_stock(args.query, not args.no_translate), ensure_ascii=False, indent=2
            )
        )
    elif args.command == "market":
        print(
            json.dumps(
                service.tools(date.fromisoformat(args.as_of)).get_stock_market(
                    args.ticker, args.period
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "historical-valuation":
        print(
            json.dumps(
                service.tools(date.fromisoformat(args.as_of)).get_historical_valuation(
                    args.ticker, args.period
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "prepare":
        print(
            json.dumps(
                service.discovery.prepare(args.ticker, date.fromisoformat(args.as_of), args.years),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "index":
        passages = service.corpus.passages(date.today())
        with service.retriever.lock:
            vectors = service.retriever._vectors(passages) if passages else None
        print(
            json.dumps(
                {
                    "passages": len(passages),
                    "dense_ready": vectors is not None,
                    "embedding_unavailable": service.retriever.model_error,
                }
            )
        )
    elif args.command == "ingest":
        from .ingestion import SecFetcher, acquire_company

        fetcher = SecFetcher(settings.home / "sec-cache", settings.sec_user_agent)
        entries = acquire_company(fetcher, args.ticker, args.years, args.offline, service.domain)
        for entry in entries:
            from .models import Fact, Filing, Passage, StatementFact

            result = service.corpus.ingest(
                Filing.model_validate(entry["filing"]),
                [Fact.model_validate(f) for f in entry["facts"]],
                [Passage.model_validate(p) for p in entry["passages"]],
                [StatementFact.model_validate(f) for f in entry.get("statement_facts", [])],
            )
            print(json.dumps(result))
    elif args.command == "evaluate":
        from .evaluation import evaluate, evaluate_integrity

        if args.suite == "all":
            suites = {"snapshot": evaluate(service), "integrity": evaluate_integrity(service)}
            report = {
                "scope": "snapshot and synthetic integrity checks; not an LLM accuracy estimate",
                "passed": sum(s["passed"] for s in suites.values()),
                "total": sum(s["total"] for s in suites.values()),
                "suites": suites,
            }
        else:
            report = evaluate(service) if args.suite == "snapshot" else evaluate_integrity(service)
        content = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(content, encoding="utf-8")
        print(content)
        if report["passed"] != report["total"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
