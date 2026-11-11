from __future__ import annotations

import argparse
import ipaddress
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


def is_loopback_host(host: str) -> bool:
    """The service has no authentication or tenant isolation."""
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bind_allowed(host: str, allow_non_loopback: bool = False) -> bool:
    """Keep loopback safe by default while supporting container networking."""
    return is_loopback_host(host) or allow_non_loopback


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
    web.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow a container or trusted reverse proxy to bind beyond loopback; add auth/TLS externally.",
    )
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    mcp.add_argument("--port", type=int, default=8124)
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow streamable HTTP outside loopback; FC_AUTH_TOKEN and TLS/proxy controls are still required.",
    )
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
    monitor_scan = commands.add_parser(
        "monitor-scan", help="Scan saved companies and build disclosure briefs"
    )
    monitor_scan.add_argument("tickers", nargs="*")
    monitor_scan.add_argument("--as-of", default=date.today().isoformat())
    monitor_scan.add_argument("--no-bootstrap", action="store_true")
    monitor_scan.add_argument("--no-brief", action="store_true")
    monitor_scan.add_argument("--reprocess", action="store_true")
    monitor_scan.add_argument("--use-model", action="store_true", default=None)
    monitor_scan.add_argument("--output", type=Path)
    monitor_replay = commands.add_parser(
        "monitor-replay", help="Import and review a historical date interval"
    )
    monitor_replay.add_argument("tickers", nargs="*")
    monitor_replay.add_argument("--start", required=True)
    monitor_replay.add_argument("--end", required=True)
    monitor_replay.add_argument("--use-model", action="store_true", default=None)
    monitor_replay.add_argument("--no-brief", action="store_true")
    monitor_replay.add_argument("--output", type=Path)
    monitor_cases = commands.add_parser(
        "monitor-cases", help="List disclosure monitoring review cases"
    )
    monitor_cases.add_argument("--status")
    monitor_cases.add_argument("--ticker")
    monitor_cases.add_argument("--limit", type=int, default=100)
    monitor_case = commands.add_parser(
        "monitor-case", help="Show one disclosure monitoring case"
    )
    monitor_case.add_argument("case_id")
    monitor_review = commands.add_parser(
        "monitor-review", help="Approve, dismiss or reopen a monitoring case"
    )
    monitor_review.add_argument("case_id")
    monitor_review.add_argument("action", choices=["approve", "dismiss", "reopen", "feedback"])
    monitor_review.add_argument("--actor", default="cli")
    monitor_review.add_argument("--reason", default="")
    monitor_review.add_argument("--comment", default="")
    monitor_review.add_argument("--brief-version", type=int)
    monitor_runs = commands.add_parser(
        "monitor-runs", help="Show recent monitoring scan runs"
    )
    monitor_runs.add_argument("--limit", type=int, default=20)
    monitor_digest = commands.add_parser(
        "monitor-digest", help="Build or deliver the daily monitoring digest"
    )
    monitor_digest.add_argument("--start")
    monitor_digest.add_argument("--end")
    monitor_digest.add_argument(
        "--channel",
        choices=["local", "webhook", "slack", "smtp"],
        default="local",
    )
    monitor_digest.add_argument("--force", action="store_true")
    monitor_digest.add_argument("--pdf", type=Path)
    monitor_digest.add_argument("--output", type=Path)
    monitor_verify = commands.add_parser(
        "monitor-verify", help="Verify an exported monitoring case package offline"
    )
    monitor_verify.add_argument("path", type=Path)
    monitor_deliver = commands.add_parser(
        "monitor-deliver", help="Record or send a monitoring case brief"
    )
    monitor_deliver.add_argument("case_id")
    monitor_deliver.add_argument(
        "--channel", choices=["local", "webhook"], default="local"
    )
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
        if not bind_allowed(args.host, args.allow_non_loopback):
            parser.error(
                "refusing a non-loopback bind: FilingCopilot has no authentication "
                "or tenant isolation; use a loopback reverse proxy with its own auth"
            )
        if not is_loopback_host(args.host) and not service.settings.auth_token:
            parser.error(
                "non-loopback web/API bind requires FC_AUTH_TOKEN; use an authenticated "
                "TLS proxy for stronger identity controls"
            )
        import uvicorn

        from .api import create_app

        scheduler = None
        settings = getattr(service, "settings", None)
        interval = int(getattr(settings, "monitoring_scan_interval_minutes", 0) or 0)
        digest_enabled = bool(
            getattr(settings, "monitoring_digest_enabled", False)
        )
        if settings is not None and getattr(settings, "monitoring_enabled", False) and (
            interval > 0 or digest_enabled
        ):
            from .monitoring.scheduler import MonitoringScheduler

            scheduler = MonitoringScheduler(
                service,
                interval,
                digest_enabled=digest_enabled,
                digest_hour=int(
                    getattr(settings, "monitoring_digest_hour", 7) or 7
                ),
                digest_channel=str(
                    getattr(settings, "monitoring_digest_channel", "local")
                    or "local"
                ),
            )
            scheduler.start()
        try:
            uvicorn.run(create_app(service), host=args.host, port=args.port)
        finally:
            if scheduler is not None:
                scheduler.stop()
    elif args.command == "mcp":
        from .mcp_server import create_mcp

        if args.transport == "streamable-http":
            if not bind_allowed(args.host, args.allow_non_loopback):
                parser.error(
                    "refusing a non-loopback MCP HTTP bind; use --allow-non-loopback explicitly"
                )
            if not is_loopback_host(args.host) and not service.settings.auth_token:
                parser.error(
                    "non-loopback MCP HTTP requires FC_AUTH_TOKEN; use an authenticated TLS proxy for stronger identity controls"
                )
        create_mcp(service, args.port, host=args.host).run(transport=args.transport)
    elif args.command == "monitor-scan":
        from .monitoring.models import ScanRequest

        result = service.monitoring.scan(
            ScanRequest(
                tickers=args.tickers or None,
                as_of=date.fromisoformat(args.as_of),
                bootstrap=not args.no_bootstrap,
                generate_brief=not args.no_brief,
                reprocess=args.reprocess,
                use_model=args.use_model,
            )
        )
        content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        print(content)
    elif args.command == "monitor-replay":
        from .monitoring.models import ReplayRequest

        result = service.monitoring.replay(
            ReplayRequest(
                tickers=args.tickers or None,
                start=date.fromisoformat(args.start),
                end=date.fromisoformat(args.end),
                generate_brief=not args.no_brief,
                use_model=args.use_model,
            )
        )
        content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        print(content)
    elif args.command == "monitor-cases":
        print(
            json.dumps(
                {
                    "cases": service.monitoring.list_cases(
                        status=args.status, ticker=args.ticker, limit=args.limit
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "monitor-case":
        print(json.dumps(service.monitoring.get_case(args.case_id), ensure_ascii=False, indent=2))
    elif args.command == "monitor-review":
        from .monitoring.models import ReviewRequest

        print(
            json.dumps(
                service.monitoring.review_case(
                    args.case_id,
                    ReviewRequest(
                        action=args.action,
                        actor=args.actor,
                        reason_code=args.reason,
                        comment=args.comment,
                        brief_version=args.brief_version,
                    ),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "monitor-runs":
        print(
            json.dumps(
                {"runs": service.monitoring.runs(args.limit)},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "monitor-digest":
        from datetime import date as date_type

        from .monitoring.models import DigestRequest

        request = DigestRequest(
            start=date_type.fromisoformat(args.start) if args.start else None,
            end=date_type.fromisoformat(args.end) if args.end else None,
            channel=args.channel,
            force=args.force,
        )
        if args.pdf:
            content = service.monitoring.digest_pdf(request)
            args.pdf.parent.mkdir(parents=True, exist_ok=True)
            args.pdf.write_bytes(content)
            print(str(args.pdf))
        elif args.output:
            result = service.monitoring.deliver_digest(request)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result["markdown"], encoding="utf-8")
            print(json.dumps({key: value for key, value in result.items() if key != "markdown"}, ensure_ascii=False, indent=2))
        else:
            result = service.monitoring.digest(request)
            print(result["markdown"])
    elif args.command == "monitor-verify":
        from .monitoring.verify import verify_case_package

        try:
            package = json.loads(args.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
            raise SystemExit(1) from exc
        report = verify_case_package(package)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["status"] != "consistent":
            raise SystemExit(1)
    elif args.command == "monitor-deliver":
        print(
            json.dumps(
                service.monitoring.deliver(args.case_id, args.channel),
                ensure_ascii=False,
                indent=2,
            )
        )
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
