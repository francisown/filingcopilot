"""Monitoring orchestration: policies, scans, briefs, cases and exports."""

from __future__ import annotations

import smtplib
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from email.message import EmailMessage

from .brief import BriefBuilder
from .cases import CaseManager
from .diff import FilingDiff
from .discovery import MonitoringDiscovery
from .events import EventBuilder
from .models import (
    DigestRequest,
    PolicyPayload,
    ReplayRequest,
    ReplayResult,
    ReviewRequest,
    ScanRequest,
    ScanResult,
    WatchPolicy,
)
from .store import MonitoringStore
from .taxonomy import DEFAULT_FORMS, form_root
from .verify import attach_integrity


class MonitoringService:
    def __init__(self, app):
        self.app = app
        self.corpus = app.corpus
        self.domain = app.domain
        self.company_discovery = app.discovery
        self.discovery = MonitoringDiscovery(self)
        self.market = app.market
        self.settings = app.settings
        self.gateway = app.gateway
        self.today = date.today()
        self.store = MonitoringStore(app.corpus)
        self.diff = FilingDiff(self, self.store)
        self.events = EventBuilder(self, self.store)
        self.briefs = BriefBuilder(self)
        self.cases = CaseManager(self.store)

    def tools(self, as_of):
        return self.app.tools(as_of)

    # Policies ---------------------------------------------------------------
    def _watch_tickers(self) -> list[str]:
        return [item["ticker"] for item in self.app.watchlist.items()["items"]]

    def _default_policy(self, ticker: str) -> WatchPolicy:
        return WatchPolicy(ticker=ticker, forms=list(DEFAULT_FORMS))

    def ensure_policies(self) -> None:
        for ticker in self._watch_tickers():
            if self.store.get_policy(ticker) is not None:
                continue
            listing = self.market.directory.listing(ticker)
            if listing is not None and not listing.filings_supported:
                continue
            # v1 is intentionally US 10-K/10-Q/8-K only. 20-F issuers are not
            # silently monitored with a form set they do not file.
            if listing is not None and getattr(listing, "annual_form", "10-K") != "10-K":
                continue
            self.store.upsert_policy(self._default_policy(ticker))

    def policies(self) -> list[dict]:
        self.ensure_policies()
        return [item.model_dump() for item in self.store.list_policies()]

    def set_policy(self, payload: PolicyPayload) -> dict:
        base = self.store.get_policy(payload.ticker) or self._default_policy(payload.ticker)
        updates = payload.model_dump(exclude_none=True)
        updates.pop("ticker", None)
        if updates and any(
            base.model_dump().get(key) != value for key, value in updates.items()
        ):
            updates["version"] = base.version + 1
        result = base.model_copy(update=updates)
        return self.store.upsert_policy(result).model_dump()

    # Scan -------------------------------------------------------------------
    def scan(self, request: ScanRequest) -> ScanResult:
        if not self.settings.monitoring_enabled:
            raise ValueError("监控已通过 FC_MONITORING_ENABLED=false 关闭。")
        as_of = request.as_of or date.today()
        if as_of > date.today():
            raise ValueError("知悉日期不能晚于今天。")
        self.today = as_of
        self.ensure_policies()
        tickers = request.tickers or self._watch_tickers()
        if not tickers:
            # A scheduled scan should follow saved monitoring policies even if
            # the separate personal watchlist is empty.
            tickers = [
                item.ticker
                for item in self.store.list_policies()
                if item.enabled
            ]
        if not tickers:
            raise ValueError("自选列表为空；请先添加证券或指定 tickers。")
        started = time.perf_counter()
        run_id = self.store.start_run(
            {
                "as_of": as_of.isoformat(),
                "tickers": tickers,
                "forms": request.forms,
                "bootstrap": request.bootstrap,
            }
        )
        result = ScanResult(run_id=run_id, as_of=as_of.isoformat(), tickers=tickers)
        for ticker in tickers:
            try:
                policy = self.store.get_policy(ticker)
                if policy is None:
                    policy = self._default_policy(ticker)
                    self.store.upsert_policy(policy)
                if not policy.enabled:
                    continue
                forms = request.forms or policy.forms
                refresh = self.discovery.refresh(ticker, as_of, forms)
                result.warnings.extend(refresh["warnings"])
                roots = {form_root(form) for form in forms}
                local = [
                    item
                    for item in self.corpus.catalog(as_of, ticker)
                    if form_root(item["form"]) in roots
                ]
                by_id = {item["id"]: item for item in local}
                metadata = {
                    item["filing_id"]: item for item in refresh["metadata"]
                }
                state = self.store.get_state(ticker)
                watermark = (state or {}).get("last_accepted")
                # Register every newly discovered accession before download.
                # A failed download therefore remains visible as pending and
                # can be retried after a later filing advances the watermark.
                accepted_values = [
                    str(item.get("accepted") or item.get("filed") or "")
                    for item in refresh["metadata"]
                    if item.get("accepted") or item.get("filed")
                ]
                max_accepted = max(accepted_values) if accepted_values else None
                for item in refresh["new"]:
                    filing_id = item.get("filing_id")
                    if not filing_id:
                        continue
                    if self.store.get_filing(ticker, filing_id) is not None:
                        continue
                    accepted = str(item.get("accepted") or item.get("filed") or "")
                    if watermark is None:
                        # First scan normally bootstraps one latest filing per
                        # form. Keep the recent 8-K batch pending so a sixth or
                        # later notice is not stranded behind the five-notice
                        # download cap.
                        recent_8k = False
                        if (
                            form_root(str(item.get("form") or "")) == "8-K"
                            and max_accepted
                            and accepted
                        ):
                            try:
                                recent_8k = (
                                    date.fromisoformat(max_accepted[:10])
                                    - date.fromisoformat(accepted[:10])
                                ).days <= 7
                            except ValueError:
                                recent_8k = False
                        status = "pending" if recent_8k else "discovered"
                    elif accepted > watermark:
                        status = "pending"
                    else:
                        continue
                    self.store.set_filing(
                        ticker,
                        filing_id,
                        status,
                        accession=item.get("accession"),
                        form=item.get("form"),
                        accepted=accepted,
                        payload={"stage": "discovered"},
                    )
                new_local = [
                    by_id[item["filing_id"]]
                    for item in refresh["new"]
                    if item["filing_id"] in by_id
                ]
                if request.reprocess:
                    # Rebuild only filings that already have a case. A manual
                    # full-history import should use bootstrap without cases.
                    candidates = [
                        item
                        for item in local
                        if self.store.get_case_by_filing(item["id"]) is not None
                    ]
                else:
                    # First scan is a bootstrap: candidates stay empty and the
                    # bootstrap block below picks one latest filing per form.
                    # Only a scan with a watermark may admit new_local rows.
                    candidates = (
                        [
                            item
                            for item in new_local
                            if str(
                                metadata.get(item["id"], {}).get("accepted") or ""
                            )
                            > watermark
                        ]
                        if watermark
                        else []
                    )
                if request.bootstrap:
                    for root in roots:
                        pool = [
                            item
                            for item in local
                            if form_root(item["form"]) == root
                        ]
                        if not pool:
                            continue
                        latest = max(
                            pool,
                            key=lambda item: (item["filed"], item["period_end"]),
                        )
                        case = self.store.get_case_by_filing(latest["id"])
                        filing_state = self.store.get_filing(ticker, latest["id"])
                        state_status = (
                            filing_state.get("status")
                            if filing_state is not None
                            else None
                        )
                        retryable = state_status in {
                            "failed",
                            "pending",
                            "discovered",
                        } or (state_status is None)
                        if latest["id"] not in {
                            item["id"] for item in candidates
                        } and (
                            (request.reprocess and case is not None)
                            or (case is None and retryable)
                        ):
                            candidates.append(latest)
                # A failed/pending accession is retried on every scan, even if
                # a newer filing advanced the watermark.
                known_candidates = {item["id"] for item in candidates}
                for row in self.store.list_filings(ticker):
                    if (
                        row["status"] in {"failed", "pending"}
                        and row["filing_id"] in by_id
                        and row["filing_id"] not in known_candidates
                    ):
                        candidates.append(by_id[row["filing_id"]])
                if not watermark and not request.reprocess:
                    result.warnings.append(
                        f"{ticker} 首次监控只处理最新 10-K/10-Q/8-K；"
                        "历史 filing 不自动建案。"
                    )
                seen: set[str] = set()
                ordered = []
                for item in sorted(
                    candidates, key=lambda value: value["filed"], reverse=True
                ):
                    if item["id"] not in seen:
                        ordered.append(item)
                        seen.add(item["id"])
                result.filings_seen += len(ordered)
                result.new_filings += len(ordered)
                for filing in ordered:
                    meta = metadata.get(filing["id"], {})
                    accepted = str(meta.get("accepted") or filing["filed"])
                    if (
                        not request.reprocess
                        and self.store.get_case_by_filing(filing["id"]) is not None
                    ):
                        self.store.set_filing(
                            ticker,
                            filing["id"],
                            "processed",
                            accession=filing.get("accession"),
                            form=filing["form"],
                            accepted=accepted,
                        )
                        continue
                    self.store.set_filing(
                        ticker,
                        filing["id"],
                        "pending",
                        accession=filing.get("accession"),
                        form=filing["form"],
                        accepted=accepted,
                    )
                    try:
                        diff_payload = self.diff.build(filing)
                        events = self.events.build(
                            filing,
                            policy,
                            diff_payload,
                            meta,
                        )
                        actionable = self.events.actionable(events, policy)
                        if not actionable:
                            result.suppressed += 1
                            self.store.set_filing(
                                ticker,
                                filing["id"],
                                "suppressed",
                                accession=filing.get("accession"),
                                form=filing["form"],
                                accepted=accepted,
                                payload={"reason": "no_actionable_event"},
                            )
                            continue
                        stored_events = [
                            self.store.upsert_event(event) for event in actionable
                        ]
                        if diff_payload.get("amends_filing_id"):
                            self.store.supersede_events(
                                diff_payload["amends_filing_id"],
                                [event.id for event in stored_events],
                            )
                        amending = self.store.amending_event_ids(filing["id"])
                        if amending:
                            self.store.supersede_events(filing["id"], amending)
                        brief = (
                            self.briefs.build(
                                filing,
                                stored_events,
                                diff_payload,
                                use_model=(
                                    request.use_model
                                    if request.use_model is not None
                                    else self.settings.monitoring_use_model
                                ),
                            )
                            if request.generate_brief
                            else None
                        )
                        case = self.cases.ensure(
                            filing,
                            stored_events,
                            brief,
                            refresh=request.reprocess,
                        )
                        if case.created_at == case.updated_at:
                            result.cases_created += 1
                        result.events_created += len(stored_events)
                        self.store.set_filing(
                            ticker,
                            filing["id"],
                            "processed",
                            accession=filing.get("accession"),
                            form=filing["form"],
                            accepted=accepted,
                        )
                    except Exception as exc:
                        message = f"{type(exc).__name__}: {exc}"[:500]
                        self.store.set_filing(
                            ticker,
                            filing["id"],
                            "failed",
                            accession=filing.get("accession"),
                            form=filing["form"],
                            accepted=accepted,
                            error=message,
                        )
                        result.warnings.append(
                            f"{ticker} {filing['form']} {filing['period_end']} "
                            f"处理未完成：{type(exc).__name__}"
                        )
                # Do not advance the watermark when acquisition had a warning:
                # the next scan must retry the affected filing instead of
                # silently treating it as already observed.
                if not refresh["warnings"]:
                    accepted = [
                        str(item.get("accepted") or "")
                        for item in refresh["metadata"]
                        if item.get("accepted")
                    ]
                    self.store.set_state(
                        ticker,
                        max(accepted) if accepted else watermark,
                        {"last_run": run_id, "as_of": as_of.isoformat()},
                    )
            except Exception as exc:
                result.warnings.append(f"{ticker} 扫描未完成：{type(exc).__name__}")
        result.duration_ms = round((time.perf_counter() - started) * 1000)
        self.store.finish_run(run_id, "completed", result.model_dump())
        return result

    def replay(self, request: ReplayRequest) -> ReplayResult:
        """Import and review a historical date interval, not just live deltas."""
        self.today = request.end
        self.ensure_policies()
        tickers = request.tickers or self._watch_tickers()
        if not tickers:
            tickers = [item.ticker for item in self.store.list_policies() if item.enabled]
        if not tickers:
            raise ValueError("自选列表为空；请先添加证券或指定 tickers。")
        started = time.perf_counter()
        run_id = self.store.start_run(
            {
                "mode": "replay",
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "tickers": tickers,
            }
        )
        result = ReplayResult(
            run_id=run_id,
            start=request.start.isoformat(),
            end=request.end.isoformat(),
            tickers=tickers,
        )
        for ticker in tickers:
            try:
                policy = self.store.get_policy(ticker)
                if policy is None:
                    policy = self._default_policy(ticker)
                    self.store.upsert_policy(policy)
                if not policy.enabled:
                    continue
                forms = request.forms or policy.forms
                roots = {form_root(form) for form in forms}
                refresh = self.discovery.refresh(ticker, request.end, forms)
                result.warnings.extend(refresh["warnings"])
                metadata = {
                    item["filing_id"]: item for item in refresh["metadata"]
                }
                in_range = [
                    item
                    for item in refresh["metadata"]
                    if request.start.isoformat()
                    <= str(item.get("filed") or "")
                    <= request.end.isoformat()
                    and form_root(str(item.get("form") or "")) in roots
                ]
                if "8-K" in roots:
                    window_days = (request.end - request.start).days + 1
                    prepared = self.company_discovery.prepare_updates(
                        ticker,
                        request.end,
                        days=window_days,
                        limit=20,
                    )
                    result.warnings.extend(prepared.get("warnings") or [])
                    if len(prepared.get("acquired") or []) >= 20:
                        result.warnings.append(
                            f"{ticker} 历史 8-K 超过单次 20 份预算，"
                            "请缩小回放区间或分批执行。"
                        )
                if "10-Q" in roots:
                    prepared = self.company_discovery.prepare_quarterly(
                        ticker, request.end
                    )
                    result.warnings.extend(prepared.get("warnings") or [])
                if "10-K" in roots:
                    years = sorted(
                        {
                            int(str(item.get("filed") or "")[:4])
                            for item in in_range
                            if form_root(str(item.get("form") or "")) == "10-K"
                        }
                        | {
                            int(str(item.get("period_end") or "")[:4])
                            for item in in_range
                            if form_root(str(item.get("form") or "")) == "10-K"
                        }
                    )
                    prepared = self.company_discovery.prepare_annual_facts(
                        ticker, request.end, fiscal_years=years or None
                    )
                    result.warnings.extend(prepared.get("warning") and [prepared["warning"]] or [])

                local = [
                    item
                    for item in self.corpus.catalog(request.end, ticker)
                    if form_root(item["form"]) in roots
                    and request.start.isoformat()
                    <= str(item.get("filed") or "")
                    <= request.end.isoformat()
                ]
                for filing in sorted(
                    local, key=lambda item: item["filed"], reverse=True
                ):
                    result.filings_seen += 1
                    try:
                        diff_payload = self.diff.build(filing)
                        events = self.events.build(
                            filing,
                            policy,
                            diff_payload,
                            metadata.get(filing["id"]),
                        )
                        actionable = self.events.actionable(events, policy)
                        if not actionable:
                            result.suppressed += 1
                            continue
                        stored_events = [
                            self.store.upsert_event(event) for event in actionable
                        ]
                        if diff_payload.get("amends_filing_id"):
                            self.store.supersede_events(
                                diff_payload["amends_filing_id"],
                                [event.id for event in stored_events],
                            )
                        amending = self.store.amending_event_ids(filing["id"])
                        if amending:
                            self.store.supersede_events(filing["id"], amending)
                        brief = (
                            self.briefs.build(
                                filing,
                                stored_events,
                                diff_payload,
                                use_model=(
                                    request.use_model
                                    if request.use_model is not None
                                    else self.settings.monitoring_use_model
                                ),
                            )
                            if request.generate_brief
                            else None
                        )
                        case = self.cases.ensure(
                            filing, stored_events, brief, refresh=True
                        )
                        if case.created_at == case.updated_at:
                            result.cases_created += 1
                        if case.id not in result.case_ids:
                            result.case_ids.append(case.id)
                        result.events_created += len(stored_events)
                    except Exception as exc:
                        result.warnings.append(
                            f"{ticker} {filing['form']} {filing['period_end']} "
                            f"回放未完成：{type(exc).__name__}"
                        )
            except Exception as exc:
                result.warnings.append(f"{ticker} 历史回放未完成：{type(exc).__name__}")
        result.duration_ms = round((time.perf_counter() - started) * 1000)
        self.store.finish_run(run_id, "replayed", result.model_dump())
        return result

    # Read surfaces ----------------------------------------------------------
    def list_events(self, **kwargs):
        return [item.model_dump() for item in self.store.list_events(**kwargs)]

    def list_cases(self, **kwargs):
        return [item.model_dump() for item in self.cases.list(**kwargs)]

    def get_case(self, case_id: str) -> dict:
        case = self.cases.get(case_id)
        return {
            "case": case.model_dump(),
            "reviews": [item.model_dump() for item in self.cases.reviews(case_id)],
        }

    def review_case(self, case_id: str, request: ReviewRequest) -> dict:
        case = self.cases.review(case_id, request)
        return self.get_case(case.id)

    def digest(self, request: DigestRequest) -> dict:
        end = request.end or date.today()
        if request.start and request.start > end:
            raise ValueError("Digest start must not be later than end.")
        if request.start:
            start_dt = datetime.combine(
                request.start, datetime_time.min, tzinfo=timezone.utc
            )
        else:
            # Continue from the last successful delivery, not midnight today.
            # A case created yesterday afternoon therefore enters the next
            # morning digest.
            previous = self.store.last_digest(request.channel)
            if previous is not None:
                # Resume from the cutoff captured before the previous query,
                # not from the send-completion time. Updates made while the
                # mail was being sent must appear in the next digest.
                previous_end = (previous.get("payload") or {}).get("end")
                if previous_end:
                    start_dt = datetime.fromisoformat(previous_end)
                else:
                    start_dt = datetime.fromtimestamp(
                        previous["created_at"], timezone.utc
                    )
            else:
                start_dt = datetime.combine(
                    end - timedelta(days=1), datetime_time.min, tzinfo=timezone.utc
                )
        end_dt = datetime.combine(end, datetime_time.max, tzinfo=timezone.utc)
        cases = []
        for case in self.cases.list(limit=500):
            if case.status not in {"pending_review", "needs_followup", "approved", "edited"}:
                continue
            policy = self.store.get_policy(case.ticker)
            if policy is not None and (not policy.enabled or not policy.notify):
                continue
            if start_dt.isoformat() <= case.updated_at <= end_dt.isoformat():
                cases.append(case)
        lines = [f"# FilingCopilot 监控早报 {end.isoformat()}", ""]
        if not cases:
            lines.append("本时间范围内没有新的监控案件。")
        for case in cases:
            brief = case.brief
            if brief is None:
                continue
            lines.append(
                f"## {case.ticker} · {case.form} · {case.period_end} · "
                f"{'待审' if case.status == 'pending_review' else case.status}"
            )
            lines.append(f"**{brief.headline}**")
            for item in brief.what_happened[:3]:
                lines.append(f"- {item}")
            for item in brief.numbers[:3]:
                pct = (
                    f"{item.change_pct:+.1f}%"
                    if item.change_pct is not None
                    else "—"
                )
                lines.append(
                    f"- {item.label or item.metric}: {item.current_value} "
                    f"{item.unit} ({pct})"
                )
            if brief.gaps:
                lines.append(f"- 缺口：{brief.gaps[0]}")
            lines.append("")
        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "digest_date": end.isoformat(),
            "case_ids": [case.id for case in cases],
            "cases": len(cases),
            "markdown": "\n".join(lines).strip() + "\n",
        }

    def _send_digest_email(self, subject: str, markdown: str) -> None:
        settings = self.settings
        if not settings.smtp_host or not settings.smtp_from or not settings.smtp_to:
            raise ValueError("SMTP 未配置：需要 FC_SMTP_HOST / FC_SMTP_FROM / FC_SMTP_TO。")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.smtp_from
        message["To"] = settings.smtp_to
        message.set_content(markdown)
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=20
        ) as server:
            if settings.smtp_starttls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)

    def deliver_digest(self, request: DigestRequest) -> dict:
        payload = self.digest(request)
        end = payload["digest_date"]
        channel = request.channel
        digest_id, claim_state = self.store.claim_digest(
            end, channel, payload, force=request.force
        )
        if digest_id is None:
            return {
                "status": (
                    "already_sent" if claim_state == "sent" else "in_progress"
                ),
                "digest_date": end,
                "channel": channel,
                "markdown": payload["markdown"],
            }
        error = None
        try:
            if channel == "local":
                status = "sent"
            elif channel == "webhook":
                url = self.settings.monitoring_webhook_url
                if not url:
                    raise ValueError("FC_MONITORING_WEBHOOK_URL 未配置。")
                import httpx

                response = httpx.post(
                    url,
                    json={"type": "monitoring_digest", **payload},
                    timeout=15,
                )
                response.raise_for_status()
                status = "sent"
            elif channel == "slack":
                url = self.settings.slack_webhook_url
                if not url:
                    raise ValueError("FC_SLACK_WEBHOOK_URL 未配置。")
                import httpx

                response = httpx.post(
                    url,
                    json={"text": payload["markdown"]},
                    timeout=15,
                )
                response.raise_for_status()
                status = "sent"
            elif channel == "smtp":
                self._send_digest_email(
                    f"FilingCopilot 监控早报 {end}", payload["markdown"]
                )
                status = "sent"
            else:
                raise ValueError("Unsupported digest channel")
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        self.store.finish_digest(
            digest_id, status, {**payload, "error": error}
        )
        return {
            "id": digest_id,
            "digest_date": end,
            "channel": channel,
            "status": status,
            "markdown": payload["markdown"],
            "error": error,
        }

    def digest_pdf(self, request: DigestRequest) -> bytes:
        from .pdf import render_markdown_pdf

        payload = self.digest(request)
        return render_markdown_pdf(payload["markdown"])

    def runs(self, limit: int = 20) -> list[dict]:
        return self.store.list_runs(limit)

    def deliver(self, case_id: str, channel: str = "local") -> dict:
        case = self.cases.get(case_id)
        if case.brief is None:
            raise ValueError("该案件没有简报。")
        markdown = self.briefs.render_markdown(case.brief)
        payload = {
            "case_id": case.id,
            "ticker": case.ticker,
            "headline": case.brief.headline,
            "status": case.status,
            "markdown": markdown,
        }
        error = None
        if channel == "local":
            status = "recorded"
        elif channel == "webhook":
            url = self.settings.monitoring_webhook_url
            if not url:
                raise ValueError("FC_MONITORING_WEBHOOK_URL 未配置。")
            try:
                import httpx

                response = httpx.post(url, json=payload, timeout=15)
                response.raise_for_status()
                status = "sent"
            except Exception as exc:
                status = "failed"
                error = type(exc).__name__
        else:
            raise ValueError("Unsupported delivery channel")
        delivery_id = self.store.add_delivery(
            case_id,
            channel,
            status,
            {**payload, "error": error},
        )
        return {
            "id": delivery_id,
            "case_id": case_id,
            "channel": channel,
            "status": status,
            "error": error,
        }

    # Export -----------------------------------------------------------------
    def export_case(self, case_id: str) -> dict:
        case = self.cases.get(case_id)
        reviews = self.cases.reviews(case_id)
        evidence: dict[str, dict] = {}
        if case.brief is not None:
            for ref in case.brief.sources:
                item = self.corpus.evidence(ref.id, self.today)
                if item:
                    evidence[ref.id] = item
        package = {
            "schema": "monitor-case-v1",
            "generated_at": date.today().isoformat(),
            "case": case.model_dump(),
            "reviews": [item.model_dump() for item in reviews],
            "evidence": evidence,
            "brief_markdown": self.briefs.render_markdown(case.brief)
            if case.brief is not None
            else "",
        }
        return attach_integrity(package)

    def render_markdown(self, case_id: str) -> str:
        case = self.cases.get(case_id)
        if case.brief is None:
            raise ValueError("该案件没有简报。")
        return self.briefs.render_markdown(case.brief)

