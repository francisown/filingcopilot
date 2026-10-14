from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

from .company_modules import module_contract, statement_char_limit
from .execution import ResearchStopped, current_control, progress, stopped
from .fundamentals import is_stock_question
from .localization import current_language, state_language
from .model_context import model_context_json
from .models import Claim, Submission
from .tools import ResearchTools
from .updates import is_stock_updates_question, wants_update_summary
from .verification import verify_submission

if TYPE_CHECKING:
    from .agent import AgentState


class ExecutionMixin:
    @state_language
    def execute(self, state: AgentState) -> dict:
        submission = None
        for call in state["messages"][-1]["tool_calls"]:
            if stopped():
                return {**state, "action": "end"}
            name = call["function"]["name"]
            if submission is not None:
                # All IDs need responses if verification later asks the model to repair.
                # A terminal submission cancels remaining calls without consuming budget.
                state["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps({"skipped": "answer already submitted"}),
                    }
                )
                state["trace"].append(
                    {
                        "step": len(state["trace"]) + 1,
                        "stage": "tool_skipped",
                        "tool": name,
                        "reason": "answer already submitted",
                    }
                )
                continue
            started = time.perf_counter()
            arguments = {}
            try:
                state["tool_count"] += 1
                if state["tool_count"] > self.settings.max_tool_calls:
                    raise ValueError("Reached tool-call limit")
                arguments = json.loads(call["function"]["arguments"])
                state["usage"]["tool_calls"] = state["usage"].get("tool_calls", 0) + 1
                control = current_control()
                if control:
                    control.record_tool(name)
                fingerprint = json.dumps([name, arguments], sort_keys=True)
                if name != "submit_answer" and fingerprint in state["seen"]:
                    raise ValueError("Duplicate call: use the previously returned evidence")
                state["seen"].add(fingerprint)
                if wants_update_summary(state["question"]) and name != "submit_answer":
                    raise ValueError(
                        "Notice summary may only submit claims from supplied notice excerpts"
                    )
                if name == "submit_answer":
                    submission = Submission.model_validate(arguments)
                    if wants_update_summary(state["question"]):
                        try:
                            self._validate_update_summary(submission, state)
                            # Coverage and failures are computed from observed events and checks.
                            submission.gaps = []
                        except ValueError:
                            submission = None
                            raise
                    if is_stock_updates_question(state["question"]):
                        try:
                            self._validate_stock_updates(submission, state)
                        except ValueError:
                            submission = None
                            raise
                    if is_stock_question(state["question"]) and (
                        len(submission.claims) > 6
                        or any(len(c.statement) > 300 for c in submission.claims)
                    ):
                        submission = None
                        raise ValueError(
                            "Stock brief needs at most six claims, each statement at most 300 characters. Keep two or three short analysis claims and only key numeric support."
                        )
                    if state.get("module"):
                        statements = [
                            c
                            for c in submission.claims
                            if c.kind in ("analysis", "narrative") and c.statement
                        ]
                        contract = module_contract(state["module"])
                        expected_kind = "analysis" if contract.kind == "analysis" else "narrative"
                        wrong_kind = [c.kind for c in submission.claims if c.kind in ("analysis", "narrative") and c.kind != expected_kind]
                        limit = statement_char_limit(state.get("output_language", "zh"))
                        if (
                            len(submission.claims) > 6
                            or len(statements) > 3
                            or any(len(c.statement) > limit for c in statements)
                            or wrong_kind
                        ):
                            submission = None
                            raise ValueError(
                                "Company-research module brief: submit at most six claims, with "
                                f"one to three analysis/narrative statements of at most {limit} "
                                f"characters each, using kind={expected_kind} for qualitative statements. Keep one leading conclusion and at most two "
                                "supporting points; move unsupported points into gaps instead of padding."
                            )
                    result = {"accepted_for_verification": True}
                else:
                    progress("tool", "正在执行研究工具。", tool=name)
                    result = state["tools"].call(name, arguments)
            except ResearchStopped:
                return {**state, "action": "end"}
            except (ValueError, TypeError, KeyError) as exc:
                result = {"error": str(exc)[:300], "recoverable": True}
            except Exception as exc:
                result = {
                    "error": "Tool unavailable",
                    "error_type": type(exc).__name__,
                    "recoverable": True,
                }
            state["trace"].append(
                {
                    "step": len(state["trace"]) + 1,
                    "stage": "tool",
                    "tool": name,
                    "arguments": arguments,
                    "result": result,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                }
            )
            state["messages"].append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": model_context_json(result),
                }
            )
        return {**state, "submission": submission, "action": "verify" if submission else "reason"}

    @staticmethod
    def _validate_update_summary(submission: Submission, state: AgentState, limit: int = 5) -> None:
        from .updates import summary_events, update_identity

        overview = next(
            t["result"] for t in state["trace"] if t.get("tool") == "get_company_updates"
        )
        events = summary_events(overview, state["question"], limit)
        event_ids = {key: update_identity(e) for e in events for key in e["evidence_ids"]}
        seen = set()
        if not submission.claims or len(submission.claims) > limit:
            raise ValueError("Notice summary requires one to five short Chinese narrative claims")
        for claim in submission.claims:
            if (
                claim.kind != "narrative"
                or len(claim.statement) > (160 if current_language() == "zh" else 900)
                or (
                    not re.search(r"[\u4e00-\u9fff]", claim.statement)
                    if current_language() == "zh"
                    else not re.search(r"[A-Za-z]", claim.statement)
                    or bool(re.search(r"[\u4e00-\u9fff]", claim.statement))
                    or len(claim.statement.split()) > 90
                )
                or any(key not in event_ids for key in claim.evidence_ids)
            ):
                raise ValueError(
                    "Notice summary must use short Chinese narratives and supplied notice IDs"
                )
            events = {event_ids[key] for key in claim.evidence_ids}
            if len(events) != 1 or events & seen:
                raise ValueError(
                    "Each summary must describe one distinct notice, without merging dates"
                )
            seen.update(events)

    @classmethod
    def _validate_stock_updates(cls, submission: Submission, state: AgentState) -> None:
        if (
            any(c.kind not in ("analysis", "narrative") for c in submission.claims)
            or sum(c.kind == "analysis" for c in submission.claims) > 3
            or sum(c.kind == "narrative" for c in submission.claims) > 2
            or any(len(c.statement) > 120 for c in submission.claims)
        ):
            raise ValueError(
                "Mixed stock brief allows at most three analysis and two narrative claims, "
                "each within 120 characters; code appends numeric support."
            )
        notices = [c for c in submission.claims if c.kind == "narrative"]
        if notices:
            cls._validate_update_summary(Submission(claims=notices), state, limit=2)

    @staticmethod
    def _complete_stock_updates(state: AgentState, overview: dict, focus: str) -> list[str]:
        """Preserve separately labeled source excerpts for missing/rejected notice summaries."""
        readable = [e for e in overview.get("events", []) if e["evidence_ids"]][:2]
        if not readable:
            return [
                "近 90 天公告未取得可读原文；经营研究仍可继续，公告部分请查看清单中的 SEC 原件，或单独查询“近期公司公告”。"
            ]
        covered = {
            key
            for claim in state["verified"]
            if claim["kind"] == "narrative"
            for key in claim["evidence_ids"]
        }
        missing = [e for e in readable if not covered.intersection(e["evidence_ids"])]
        if not missing:
            return []
        claims = [
            Claim(
                kind="excerpt",
                evidence_ids=[event["evidence_ids"][0]],
                statement=state["tools"].ledger[event["evidence_ids"][0]]["text"],
            )
            for event in missing
        ]
        excerpts, errors = verify_submission(
            Submission(claims=claims),
            state["tools"],
            state["question"],
            focus_ticker=focus,
            allow_excerpts=True,
        )
        for excerpt in excerpts:
            excerpt["index"] = max((c["index"] for c in state["verified"]), default=-1) + 1
            state["verified"].append(excerpt)
        state["trace"].append(
            {
                "stage": "notice_excerpt_fallback",
                "accepted_excerpts": len(excerpts),
                "errors": errors,
            }
        )
        return [
            f"{len(missing)} 份近期公告未完成可交付的中文摘要，已保留通过来源匹配的原文摘录；可单独查询“近期公司公告中文摘要”继续。",
            *errors,
        ]

    @staticmethod
    def _deadline_numeric_fallback(
        state: AgentState,
        tools: ResearchTools,
        question: str,
        focus: str | None,
        overview: dict | None,
        trailing: dict | None,
    ) -> list[dict]:
        """Keep deterministic typed figures when model interpretation misses the deadline.

        A deadline must never turn already observed facts into an unreviewed
        investment opinion.  It is still useful to return a small, auditable
        set of annual/TTM calculations and let the UI explain that the
        qualitative assessment did not finish.
        """
        candidates = []
        for source in (overview or {}, trailing or {}):
            candidates.extend(source.get("evidence", []))
        seen = set()
        typed = []
        for item in candidates:
            item_id = item.get("id")
            if not item_id or item_id in seen or item_id not in tools.ledger:
                continue
            if item.get("kind") not in ("fact", "calculation", "valuation", "market"):
                continue
            seen.add(item_id)
            typed.append(item)
        # Prefer source-bound calculations, then the latest annual/TTM facts.
        typed.sort(
            key=lambda item: (
                item.get("kind") != "calculation",
                item.get("operation") not in ("growth", "ratio", "trailing_sum"),
                -(int(item.get("fiscal_year") or 0)),
                item.get("id", ""),
            )
        )
        accepted = []
        for item in typed[:8]:
            claims = Submission(
                claims=[Claim(kind=item["kind"], evidence_ids=[item["id"]])], gaps=[]
            )
            verified, _ = verify_submission(
                claims, tools, question, focus_ticker=focus
            )
            accepted.extend(verified)
        return accepted
