from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

from .annual_details import comparable_annual_periods
from .annual_scope import annual_fact_years, compares_prior_year, explicit_years
from .company_modules import module_contract, statement_char_limit
from .execution import ResearchStopped, current_control, progress, stopped
from .fundamentals import is_stock_question
from .localization import current_language, state_language
from .localization import text as translate_text
from .model_context import model_context_json
from .models import Claim, Submission
from .submission_protocol import parse_submission
from .tools import ResearchTools
from .updates import is_stock_updates_question, wants_update_summary
from .verification import numeric_completeness_errors, verify_submission

if TYPE_CHECKING:
    from .agent import AgentState


class ExecutionMixin:
    def _complete_filing_answer(self, state: AgentState, gaps: list[str]) -> tuple[list[str], bool]:
        """Retain only verified observed math; retire only resolved numeric gaps.

        This runs after model submission/review, under the existing tool budget.
        It can add typed claims but cannot manufacture a missing explanation.
        """
        tools, question, focus = state["tools"], state["question"], state["focus_ticker"]
        stock_question = is_stock_question(question)
        fallback_complete = False
        if not stopped() and not stock_question and focus:
            calculation_ids = self._complete_observed_calculations(state, self.settings.max_tool_calls)
            if state["verified"]:
                # A valid partial submission must not suppress requested math.
                # Preserve its reviewed prose and gaps; add only bound, requested
                # calculations, passing the same verifier as model submissions.
                existing_ids = {i for c in state["verified"] if c["kind"] in ("fact", "calculation")
                                for i in c["evidence_ids"]}
                supplemented = False
                for evidence_id in calculation_ids:
                    if evidence_id in existing_ids:
                        continue
                    checked, _ = verify_submission(
                        Submission(claims=[Claim(kind="calculation", evidence_ids=[evidence_id])]),
                        tools, question, focus_ticker=focus,
                    )
                    for detail in checked:
                        detail["index"] = max((c["index"] for c in state["verified"]), default=-1) + 1
                        state["verified"].append(detail)
                        existing_ids.add(evidence_id)
                    if checked:
                        supplemented = True
                        state["trace"].append({
                            "stage": "observed_calculation_completion",
                            "evidence_id": evidence_id,
                            "accepted_claims": len(checked),
                        })
                if supplemented and state.get("numeric_coverage_errors"):
                    # Resolve only machine-checked omissions that the delivered
                    # calculation actually filled. Never infer that prose gaps
                    # or independent-review warnings have disappeared.
                    remaining = numeric_completeness_errors(
                        state["verified"], tools, question, focus,
                    )
                    resolved = set(state["numeric_coverage_errors"]) - set(remaining)
                    if resolved:
                        gaps = [gap for gap in gaps if gap not in resolved]
                        state["errors"] = [error for error in state["errors"] if error not in resolved]
                        state["trace"].append({"stage": "numeric_coverage_completed",
                                               "resolved": sorted(resolved), "remaining": remaining})
                if supplemented and gaps:
                    # Free-form gaps can combine missing math with missing
                    # interpretation. Preserve their content and partial status,
                    # but date the explanation so it cannot deny a delivered sum.
                    state["trace"].append({"stage": "pre_calculation_gaps", "gaps": list(gaps)})
                    prefix = translate_text("补充计算前记录的说明（已展示的计算现已补齐，其余缺口仍保留）：")
                    gaps = [prefix + gap for gap in gaps]
        if not state["verified"] and not stopped() and not stock_question and focus:
            # The model may end without submitting (some providers return an
            # empty tool-call with finish_reason="tool_calls"). Do not throw
            # away figures the run already observed and can verify.
            fallback = self._observed_typed_fallback(tools, question, focus)
            for detail in fallback:
                if not any(
                    detail["evidence_ids"] == existing["evidence_ids"]
                    for existing in state["verified"]
                ):
                    detail["index"] = max(
                        (c["index"] for c in state["verified"]), default=-1
                    ) + 1
                    state["verified"].append(detail)
            if fallback:
                # A numeric question fully answered from bound facts is complete;
                # only an actually uncovered question keeps the model gap.
                fallback_complete = self._fallback_covers_question(
                    tools, question, fallback, focus=focus
                )
                if not fallback_complete:
                    gaps.append(
                        "模型未在预算内提交完整回答；已保留已在本地核验的财务数字，未核验的解释不作结论。"
                    )
                state["trace"].append(
                    {
                        "stage": "observed_typed_fallback",
                        "accepted_claims": len(fallback),
                        "complete": fallback_complete,
                        "reason": "deterministic typed evidence retained after the model failed to submit",
                    }
                )
        return gaps, fallback_complete

    @staticmethod
    def _prepare_annual_comparison(tools, question, focus, max_calls):
        """Read local typed premises before causal drafting, within the tool budget."""
        years = explicit_years(question)
        metrics, _ = tools.domain.requests(question)
        if len(years) != 1 or not metrics or not compares_prior_year(question):
            return None, []
        # Leave room for source retrieval and a submission. Never start a
        # comparison whose necessary inputs do not fit in the available budget.
        if 3 * len(metrics) + 2 > max_calls:
            return None, []
        evidence, trace, missing = [], [], []

        def call(name, arguments):
            control = current_control()
            if control:
                control.record_tool(name)
            try:
                result = tools.call(name, arguments)
            except ValueError as exc:
                trace.append({"stage": "tool", "tool": name, "arguments": arguments, "error": str(exc)})
                raise
            trace.append({"stage": "tool", "tool": name, "arguments": arguments, "result": result})
            return result

        year = next(iter(years))
        for metric in sorted(metrics):
            pair = []
            for source_year in (year, year - 1):
                if stopped():
                    return {"evidence": evidence, "missing": missing}, trace
                result = call("get_financial_fact", {"ticker": focus, "metric": metric, "fiscal_year": source_year})
                if result["found"]:
                    pair.append(result["evidence"])
                    evidence.append(result["evidence"])
                else:
                    missing.append({"metric": metric, "fiscal_year": source_year})
            if len(pair) == 2 and comparable_annual_periods(*pair) and not stopped():
                try:
                    result = call("calculate", {"operation": "growth", "left_id": pair[0]["id"], "right_id": pair[1]["id"]})
                    evidence.append(result["evidence"])
                except ValueError as exc:
                    missing.append({"metric": metric, "operation": "growth", "reason": str(exc)})
        return {"evidence": evidence, "missing": missing}, trace

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
                    submission = parse_submission(arguments)
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

    @staticmethod
    def _complete_observed_calculations(state: dict, max_tool_calls: int) -> list[str]:
        """Use remaining tool budget for requested math on unambiguous observed facts.

        No acquisition, guessing, model prose or bypass of the calculation tool's
        period/unit rules is allowed here. Missing or competing versions stay gaps.
        """
        tools, question, focus = state["tools"], state["question"], state["focus_ticker"]
        years = {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)}
        if not focus or not years:
            return []
        metrics, ratios = tools.domain.requests(question)
        facts = {}
        for item in tools.ledger.values():
            if item.get("kind") == "fact" and item.get("ticker") == focus:
                facts.setdefault((item.get("metric"), item.get("fiscal_year")), []).append(item)
        pairs = []
        for year in sorted(years):
            if compares_prior_year(question):
                pairs.extend(("growth", (metric, year), (metric, year - 1))
                             for metric in sorted(metrics))
            pairs.extend(("ratio", (tools.domain.ratios[key].numerator, year),
                          (tools.domain.ratios[key].denominator, year)) for key in sorted(ratios))
        requested_ids = []
        for operation, left, right in pairs:
            if stopped():
                break
            if len(facts.get(left, [])) != 1 or len(facts.get(right, [])) != 1:
                continue
            ids = [facts[left][0]["id"], facts[right][0]["id"]]
            existing = next((item for item in tools.ledger.values()
                             if item.get("operation") == operation and item.get("input_ids") == ids), None)
            if existing:
                requested_ids.append(existing["id"])
                continue
            if state["tool_count"] >= max_tool_calls:
                continue
            arguments = {"operation": operation, "left_id": ids[0], "right_id": ids[1]}
            state["tool_count"] += 1
            state["usage"]["tool_calls"] = state["usage"].get("tool_calls", 0) + 1
            control = current_control()
            if control:
                control.record_tool("calculate")
            started = time.perf_counter()
            trace = {"stage": "fallback_calculation", "tool": "calculate", "arguments": arguments}
            try:
                trace["result"] = tools.calculate(**arguments)
                requested_ids.append(trace["result"]["evidence"]["id"])
            except ValueError as exc:
                trace["error"] = str(exc)
            trace["duration_ms"] = round((time.perf_counter() - started) * 1000)
            state["trace"].append(trace)
        return requested_ids

    @staticmethod
    def _observed_typed_fallback(
        tools: ResearchTools, question: str, focus: str | None
    ) -> list[dict]:
        """Keep already-observed typed evidence when the model fails to submit.

        Only facts/calculations bound to the focused ticker are eligible, and
        only those for fiscal years the question actually names (when it names
        any). Every claim still passes the ordinary verifier, so an unverified
        number is never delivered. This mirrors ``_deadline_numeric_fallback``:
        the model's missing interpretation must not silently erase figures the
        run already observed and checked.
        """
        if not focus:
            return []
        years = explicit_years(question)
        fact_years = annual_fact_years(question)
        seen: set[str] = set()
        typed = []
        for item in tools.ledger.values():
            item_id = item.get("id")
            if (
                item.get("ticker") != focus
                or item.get("kind") not in ("fact", "calculation")
                or not item_id
                or item_id in seen
            ):
                continue
            year = item.get("fiscal_year")
            allowed_years = fact_years if item.get("kind") == "fact" else years
            if allowed_years and year is not None and int(year) not in allowed_years:
                continue
            seen.add(item_id)
            typed.append(item)
        typed.sort(
            key=lambda item: (
                item.get("kind") != "calculation",
                -(int(item.get("fiscal_year") or 0)),
                item.get("id", ""),
            )
        )
        accepted = []
        for item in typed[:8]:
            claims = Submission(
                claims=[Claim(kind=item["kind"], evidence_ids=[item["id"]])], gaps=[]
            )
            verified, _ = verify_submission(claims, tools, question, focus_ticker=focus)
            accepted.extend(verified)
        return accepted

    @staticmethod
    def _fallback_covers_question(
        tools: ResearchTools, question: str, claims: list[dict], focus: str | None = None
    ) -> bool:
        """Only explicit annual numeric requests can be completed by this fallback.

        Unknown wording stays partial. Valid individual claims alone do not prove
        coverage: recheck the whole set for every company, year, metric and ratio.
        Without an explicit year, observed facts cannot establish "latest".
        """
        from .annual_numbers import numeric_annual_question

        if not claims or not re.search(r"(?<!\d)20\d{2}(?!\d)", question):
            return False
        if not numeric_annual_question(question, tools.domain):
            return False
        terms = []
        for ticker, company in tools.domain.companies.items():
            terms.extend([ticker, company.name, *company.aliases])
        for key, spec in {**tools.domain.metrics, **tools.domain.ratios}.items():
            terms.extend([key, key.replace("_", " "), spec.label, *spec.aliases])
        residual = question
        for term in sorted(set(terms), key=len, reverse=True):
            if not term:
                continue
            pattern = re.escape(term)
            if term.isascii():
                pattern = r"(?<![a-z0-9_])" + pattern + r"(?![a-z0-9_])"
            residual = re.sub(pattern, " ", residual, flags=re.I)
        residual = re.sub(r"(?:FY\s*)?20\d{2}", " ", residual, flags=re.I)
        residual = re.sub(
            r"\b(?:what|is|are|was|were|the|and|of|for|in|please|show|give|list|"
            r"compare|versus|vs|how|much|fiscal|year|annual|amounts?|values?|"
            r"growth|rate|year-over-year|previous|prior)\b|'s", " ", residual, flags=re.I,
        )
        residual = re.sub(
            r"分别|是多少|多少|数值|金额|列出|上一财年|上一年|财年|年度|同比|增长率|增长|比较|"
            r"请|的|和|与|及|为|是|年", " ", residual,
        )
        if re.search(r"\w", residual):
            return False
        ids = list(dict.fromkeys(eid for claim in claims for eid in claim["evidence_ids"]))
        items = [tools.ledger.get(eid) for eid in ids]
        if any(not item or item.get("kind") not in {"fact", "calculation"} for item in items):
            return False
        submission = Submission(claims=[
            Claim(kind=item["kind"], evidence_ids=[item["id"]]) for item in items
        ])
        # The ordinary verifier uses this canonical spelling for growth coverage.
        coverage_question = question
        if re.search(r"growth|同比|增长|year-over-year", question, re.I):
            coverage_question += " 同比"
        accepted, errors = verify_submission(
            submission, tools, coverage_question, focus_ticker=focus
        )
        return not errors and len(accepted) == len(submission.claims)
