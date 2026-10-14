from __future__ import annotations

import hashlib
import json
import time
import uuid

from .audit import create_package
from .execution import ResearchControl, ResearchStopped, progress, research_scope
from .fundamentals import is_stock_question
from .localization import prepare_answer, request_language
from .models import Claim, FollowupRequest, ResearchAnswer, ResearchRequest, Submission
from .status import classify_status_reason
from .verification import verify_submission


class ResearchMixin:
    def _publish_cached_preview(self, request):
        from .preview import cached_financial_preview

        try:
            preview = cached_financial_preview(self.corpus, self.retriever, request)
        except (ValueError, KeyError):
            progress("preview_unavailable", "缓存年度数字未能完成核验，继续完整研究。")
            return
        if preview:
            progress("verified_preview", "已核验的缓存年度数字可先查看，研究仍在进行。", preview=preview)

    def _deadline_company_fallback(self, request, control, started):
        """Return local source material when a full report reaches its deadline.

        Full-company preparation can spend the entire budget before the report
        draft is committed.  Re-open only the local corpus with the research
        scope cleared: no network refresh, model call, or unverified prose is
        allowed here.  Typed facts/calculations and exact business excerpts
        remain useful and auditable while the six-section summary is retried.
        """
        ticker = (request.ticker or "").upper()
        if not ticker:
            scope = self.refresh_domain().company_scope(request.question)
            ticker = next(iter(scope)).upper() if len(scope) == 1 else ""
        if not ticker:
            return None
        tools = self.tools(request.as_of)
        tools.fact_loader = None
        tools.annual_source_loader = None
        answer, ids = [], set()
        with research_scope(None):
            try:
                overview = tools.get_company_overview(ticker)
            except Exception:
                overview = {"evidence": []}
            for compact in overview.get("evidence", []):
                item_id = compact.get("id")
                if not item_id:
                    continue
                item = tools.ledger.get(item_id) or self.corpus.evidence(item_id, request.as_of)
                if item:
                    tools.ledger[item_id] = item
                    ids.add(item_id)
            # The business card gets exact source sentences even when the
            # prose model never started.  This is source material, not a
            # generated business-model conclusion.
            filings = [
                row for row in tools.corpus.catalog(request.as_of, ticker)
                if row["form"] in self.refresh_domain().annual_forms(ticker)
            ]
            annual = max(filings, key=lambda row: (row["period_end"], row["filed"], row["id"]), default=None)
            if annual:
                hits = tools.retriever.search(
                    "business products services segments customers revenue",
                    request.as_of,
                    ticker,
                    3,
                    filing_id=annual["id"],
                    lexical_only=True,
                ).get("hits", [])
                for hit in hits:
                    item_id = hit["passage"]["id"]
                    item = self.corpus.evidence(item_id, request.as_of)
                    if item:
                        tools.ledger[item_id] = item
                        ids.add(item_id)
                        checked, _ = verify_submission(
                            Submission(
                                claims=[
                                    Claim(
                                        kind="excerpt",
                                        evidence_ids=[item_id],
                                        statement=item["text"],
                                    )
                                ]
                            ),
                            tools,
                            request.question,
                            focus_ticker=ticker,
                            allow_excerpts=True,
                        )
                        answer.extend(checked)
            numeric = [
                item
                for item_id, item in tools.ledger.items()
                if item_id in ids
                and item.get("kind") in ("fact", "calculation")
                and item.get("operation") in (None, "growth", "ratio")
            ]
            numeric.sort(key=lambda item: (-int(item.get("fiscal_year") or 0), item["id"]))
            for item in numeric[:8]:
                checked, _ = verify_submission(
                    Submission(claims=[Claim(kind=item["kind"], evidence_ids=[item["id"]])]),
                    tools,
                    request.question,
                    focus_ticker=ticker,
                )
                answer.extend(checked)
        evidence = list(tools.ledger.values())
        trace = [
            {
                "stage": "deadline_company_fallback",
                "accepted_claims": len(answer),
                "source_material_only": True,
                "reason": "full report deadline; local typed evidence and exact business excerpts retained",
            }
        ]
        gaps = [
            "完整公司文字总结未在本轮时间预算内完成；已保留可核验数字和商业模式原文材料，可单独生成各主题总结后重试。"
        ]
        if not answer:
            gaps.append("本地语料中尚未形成可交付的商业模式或财务证据。")
        return (
            ResearchAnswer(
                id="run-" + uuid.uuid4().hex,
                question=request.question,
                as_of=request.as_of,
                mode="company_report",
                status="partial" if answer else "abstained",
                answer=answer,
                gaps=gaps,
                evidence=evidence,
                trace=trace,
                usage=dict(control.usage),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                model=None,
                research_type="stock",
            ),
            self.refresh_domain(),
            ticker,
        )

    @request_language
    def research(self, request: ResearchRequest, control=None) -> ResearchAnswer:
        started = time.perf_counter()
        control = control or ResearchControl()
        control.start_budget(self.settings.research_timeout)
        with research_scope(control):
            try:
                self._publish_cached_preview(request)
                answer, domain, focus = self._research(request)
            except ResearchStopped:
                fallback = (
                    self._deadline_company_fallback(request, control, started)
                    if request.depth == "full"
                    else None
                )
                if fallback:
                    progress(
                        "deadline_company_fallback",
                        "完整报告达到本轮预算；正在保留已核验数字和原文材料。",
                    )
                    answer, domain, focus = fallback
                else:
                    answer = ResearchAnswer(
                    id="run-" + uuid.uuid4().hex,
                    question=request.question,
                    as_of=request.as_of,
                    mode=request.mode,
                    status="abstained",
                    answer=[],
                    gaps=[],
                    evidence=[],
                    trace=[],
                    usage=dict(control.usage) if control else {},
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    research_type="stock" if is_stock_question(request.question) else "filing",
                    )
                    domain, focus = self.refresh_domain(), request.ticker
            answer.elapsed_ms = round((time.perf_counter() - started) * 1000)
            if control.cancelled or control.expired:
                answer.trace.append(
                    {
                        "stage": "execution_events",
                        "events": list(control.events),
                        "total_events": control.event_count,
                        "retained_events": len(control.events),
                    }
                )
            answer = control.finalize(answer)
            # The finalizer may add a deadline/cancellation event after the
            # agent or a deterministic tool created the answer.  Recompute the
            # reason at this boundary so the UI explains the actual outcome.
            answer.status_reason = classify_status_reason(answer)
            if control.expired:
                answer.trace[-1]["focus_ticker"] = focus
            # Persist an auditable execution receipt with every research run.
            # The receipt intentionally contains counts and timings only, never
            # prompts, credentials, or provider response bodies.
            usage = dict(answer.usage or {})
            trace_tools = sum(1 for item in answer.trace if item.get("tool"))
            usage["model_calls"] = max(int(usage.get("model_calls") or 0), control.usage["model_calls"])
            usage["tool_calls"] = max(int(usage.get("tool_calls") or 0), control.usage.get("tool_calls", 0), trace_tools)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] = max(int(usage.get(key) or 0), int(control.usage.get(key) or 0))
            usage["elapsed_ms"] = answer.elapsed_ms
            usage["model_latency_ms"] = sum(
                int(item.get("duration_ms") or 0)
                for item in answer.trace
                if item.get("model") or item.get("stage") in ("reason", "semantic_review", "company_report_draft")
            )
            usage["tool_latency_ms"] = sum(
                int(item.get("duration_ms") or 0) for item in answer.trace if item.get("tool")
            )
            usage["time_budget_seconds"] = control.time_budget
            usage["remaining_seconds"] = round(control.remaining or 0.0, 3)
            usage["budget_status"] = (
                "cancelled" if control.cancelled else "expired" if control.expired else "completed"
            )
            input_price = self.settings.model_input_price_per_million
            output_price = self.settings.model_output_price_per_million
            if input_price is not None or output_price is not None:
                usage["actual_cost_usd"] = round(
                    (usage["prompt_tokens"] / 1_000_000) * (input_price or 0)
                    + (usage["completion_tokens"] / 1_000_000) * (output_price or 0),
                    6,
                )
            answer.usage = usage
            progress(
                "saving", "正在保存回答、证据和可复核研究包。", accepted_claims=len(answer.answer)
            )
            prepare_answer(answer, request.language)
            self.corpus.save_run(
                answer.model_dump(mode="json"), create_package(answer, domain, focus)
            )
            progress(
                "saved",
                "研究结果已保存，可查看引用或导出研究包。",
                accepted_claims=len(answer.answer),
            )
            return answer

    @staticmethod
    def _evidence_version(evidence: list[dict]) -> str:
        body = [
            {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "source_sha256": item.get("source_sha256")
                or item.get("filing", {}).get("content_sha256"),
            }
            for item in evidence
        ]
        return hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @request_language
    def followup(self, request: FollowupRequest) -> ResearchAnswer:
        """Continue a saved run by evidence ID, optionally creating a new version.

        The default path is intentionally local and cheap: it carries prior
        claims and their IDs forward without re-acquiring every filing. A
        refresh is explicit and produces a new immutable run so attribution can
        compare it with the old one.
        """
        previous = self.corpus.run(request.research_id)
        if previous is None:
            raise ValueError("找不到前一轮研究；请先从研究记录打开有效的 research_id。")
        prior = ResearchAnswer.model_validate(previous)
        prior_as_of = prior.as_of
        as_of = request.as_of or prior_as_of
        if as_of < prior_as_of:
            raise ValueError("追问的知悉日期不能早于前一轮研究；请使用原日期或更新日期。")
        prior_evidence = list(prior.evidence)
        prior_ids = [item["id"] for item in prior_evidence if item.get("id")]
        if not prior_ids:
            raise ValueError("前一轮没有可绑定的证据账本，无法安全追问。")
        prior_version = self._evidence_version(prior_evidence)
        focus = next((item.get("ticker") for item in prior_evidence if item.get("ticker")), None)
        if request.refresh_sources:
            refreshed = ResearchRequest(
                question=request.question,
                ticker=focus,
                as_of=as_of,
                mode=request.mode,
                auto_acquire=True,
                language=request.language,
            )
            answer = self.research(refreshed)
            cumulative_usage = {
                key: int(prior.usage.get(key) or 0) + int(answer.usage.get(key) or 0)
                for key in ("model_calls", "tool_calls", "prompt_tokens", "completion_tokens", "total_tokens")
            }
            current_ids = [item["id"] for item in answer.evidence if item.get("id")]
            current_version = self._evidence_version(answer.evidence)
            old_by_id = {item.get("id"): item for item in prior_evidence}
            changed_ids = [
                item.get("id")
                for item in answer.evidence
                if item.get("id") in old_by_id and old_by_id[item["id"]] != item
            ]
            new_ids = [item_id for item_id in current_ids if item_id not in prior_ids]
            answer.followup = {
                "research_id": request.research_id,
                "prior_question": prior.question,
                "followup_question": request.question,
                "prior_as_of": prior_as_of.isoformat(),
                "as_of": as_of.isoformat(),
                "prior_evidence_version": prior_version,
                "evidence_version": current_version,
                "prior_evidence_ids": prior_ids,
                "new_evidence_ids": new_ids,
                "changed_evidence_ids": changed_ids,
                "version_status": "changed" if current_version != prior_version else "unchanged",
                "round": int((prior.followup or {}).get("round", 0)) + 1,
                "context_mode": "evidence_ids",
                "reused_prior": False,
                "cumulative_usage": cumulative_usage,
                "attribution": {
                    "available": True,
                    "before_run_id": request.research_id,
                    "after_run_id": answer.id,
                },
            }
            answer.trace.insert(
                0,
                {
                    "stage": "followup_refresh",
                    "prior_research_id": request.research_id,
                    "prior_evidence_version": prior_version,
                    "new_evidence_ids": new_ids,
                    "changed_evidence_ids": changed_ids,
                    "reused_prior_evidence": True,
                },
            )
            answer.gaps.append(
                "本轮产生了新的研究版本；请打开版本归因，核对新资料与前轮结论的变化。"
            )
            package = create_package(answer, self.refresh_domain(), focus)
            self.corpus.update_run(answer.model_dump(mode="json"), package)
            return answer

        retained = [dict(claim) for claim in prior.answer]
        answer = ResearchAnswer(
            language=prior.language,
            id="run-" + uuid.uuid4().hex,
            question=request.question,
            as_of=prior_as_of,
            mode="followup",
            status="partial" if retained else "abstained",
            answer=retained,
            gaps=["本轮追问沿用前一轮证据，没有重新采集资料；如需检查新披露，请开启刷新并生成新版本。"],
            evidence=prior_evidence,
            trace=[
                {
                    "stage": "followup_context",
                    "prior_research_id": request.research_id,
                    "prior_question": prior.question,
                    "prior_as_of": prior_as_of.isoformat(),
                    "prior_evidence_version": prior_version,
                    "evidence_ids": prior_ids,
                    "reused_prior_evidence": True,
                    "new_evidence_ids": [],
                }
            ],
            usage={
                "model_calls": 0,
                "tool_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "elapsed_ms": 0,
                "budget_status": "reused_evidence",
            },
            elapsed_ms=0,
            research_type=prior.research_type,
            followup={
                "research_id": request.research_id,
                "prior_question": prior.question,
                "followup_question": request.question,
                "prior_as_of": prior_as_of.isoformat(),
                "as_of": prior_as_of.isoformat(),
                "prior_evidence_version": prior_version,
                "evidence_version": prior_version,
                "prior_evidence_ids": prior_ids,
                "new_evidence_ids": [],
                "changed_evidence_ids": [],
                "version_status": "unchanged",
                "round": int((prior.followup or {}).get("round", 0)) + 1,
                "prior_claim_indexes": [claim.get("index") for claim in retained],
                "context_mode": "evidence_ids",
                "reused_prior": True,
                "cumulative_usage": {
                    key: int(prior.usage.get(key) or 0)
                    for key in ("model_calls", "tool_calls", "prompt_tokens", "completion_tokens", "total_tokens")
                },
            },
        )
        answer.status_reason = "reused_evidence"
        self.corpus.save_run(answer.model_dump(mode="json"), create_package(answer, self.refresh_domain(), focus))
        return answer
