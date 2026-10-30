from __future__ import annotations

import json
import re
import time
from decimal import Decimal
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
from .semantic_boundaries import narrative_completeness_errors
from .submission_protocol import parse_submission
from .tools import ResearchTools
from .updates import is_stock_updates_question, wants_update_summary
from .verification import numeric_completeness_errors, verify_submission

if TYPE_CHECKING:
    from .agent import AgentState


_PLACEHOLDER_GAPS = {
    "placeholder",
    "placeholdergap",
    "tbd",
    "todo",
    "待补充",
    "待补",
    "待定",
    "暂无",
    "暂无内容",
    "未填写",
    "占位",
    "占位符",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "不详",
    "无",
    "-",
    "—",
    "…",
    "...",
}


def _is_placeholder_gap(value: object) -> bool:
    """Return true only for a gap that carries no explanation.

    Do not broaden this to substring matching: statements such as "没有披露" or
    "无法确认" are meaningful evidence boundaries and must remain visible.
    """
    text = str(value or "").strip().casefold()
    if not text:
        return True
    # Providers sometimes wrap a token in punctuation or code formatting.
    normalized = text.strip(" \t\r\n.,;:!?。！？、，；：()[]{}<>`*_\"'“”‘’")
    return normalized in _PLACEHOLDER_GAPS


def _agreed_fact(items: list[dict]) -> dict | None:
    """Return one observation when every duplicate reports the same figure.

    The completion guard exists to keep *competing* versions out of a
    calculation, not to punish a corpus that legitimately stored the same fact
    twice (same value, currency, unit and period under another id). Values that
    disagree still return None so the gap survives.
    """
    if not items:
        return None
    signatures = {
        (
            str(item.get("value")),
            item.get("unit"),
            item.get("currency"),
            item.get("start"),
            item.get("end"),
        )
        for item in items
    }
    return items[0] if len(signatures) == 1 else None


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
        model_failure_trace = any(
            item.get("stage") in ("model_error", "semantic_review_error")
            or (
                item.get("tool") == "submit_answer"
                and isinstance(item.get("result"), dict)
                and item["result"].get("error")
            )
            for item in state.get("trace", [])
        )
        if (
            not state["verified"]
            and not stopped()
            and not stock_question
            and model_failure_trace
        ):
            # Preserve source material already retrieved when both model calls
            # fail.  These are exact excerpts with an explicit partial gap;
            # they never stand in for an unverified synthesis.
            excerpts = self._observed_narrative_excerpt_fallback(state)
            for detail in excerpts:
                detail["index"] = max(
                    (claim["index"] for claim in state["verified"]), default=-1
                ) + 1
                state["verified"].append(detail)
            if excerpts:
                message = (
                    "模型请求未完成；已保留与问题直接相关的原文摘录，未生成未经核验的综合判断。"
                    if re.search(r"[\u4e00-\u9fff]", question)
                    else "The model request did not complete; directly relevant filing excerpts are retained, and no unverified synthesis is presented."
                )
                gaps.append(message)
                state["trace"].append(
                    {
                        "stage": "narrative_excerpt_fallback",
                        "accepted_excerpts": len(excerpts),
                        "reason": "exact observed filing passages retained after model failure",
                    }
                )
        if (
            not state["verified"]
            and not stopped()
            and not stock_question
            and state.get("module")
            and model_failure_trace
        ):
            excerpts = self._observed_module_excerpt_fallback(state)
            for detail in excerpts:
                detail["index"] = max(
                    (claim.get("index", -1) for claim in state["verified"]), default=-1
                ) + 1
                state["verified"].append(detail)
            if excerpts:
                gaps.append(
                    "模型整理未完成；已保留与本节直接相关的逐字原文材料，待模型恢复后可继续整理。"
                )
                state["trace"].append(
                    {
                        "stage": "module_excerpt_fallback",
                        "module": state["module"],
                        "accepted_excerpts": len(excerpts),
                        "reason": "exact observed source retained after factual module model failure",
                    }
                )
        explanation_errors = narrative_completeness_errors(state["verified"], question)
        if explanation_errors:
            # A provider can submit valid calculations while losing every
            # narrative sentence during review.  Preserve a few exact passages
            # already observed from the same filing so the user can inspect the
            # disclosed drivers, but keep the answer partial: an excerpt is
            # source material, not a synthesized causal conclusion.
            excerpts = self._observed_narrative_excerpt_fallback(state)
            if excerpts:
                for detail in excerpts:
                    detail["index"] = max(
                        (claim.get("index", -1) for claim in state["verified"]),
                        default=-1,
                    ) + 1
                    state["verified"].append(detail)
                chinese = bool(re.search(r"[\u4e00-\u9fff]", question))
                gaps.append(
                    "模型未完成原因或风险归纳；已保留同一申报中的逐字摘录，摘录不等同于综合结论。"
                    if chinese
                    else "The model did not complete the causal or risk synthesis; exact excerpts from the same filing are retained, but excerpts are not a synthesized conclusion."
                )
                state["trace"].append({
                    "stage": "narrative_excerpt_completion",
                    "accepted_excerpts": len(excerpts),
                    "reason": "numeric-only verified draft; retained exact filing passages instead of inventing synthesis",
                })
            # Exact excerpts improve inspectability but do not satisfy an
            # explanation request. Preserve the original translated blocking
            # gap so the status remains partial and the UI states the missing
            # synthesis.
            gaps.append(translate_text(
                "问题要求的原因或风险解释尚未完成；已展示的数字仅供参考，不能替代该解释。"
            ))
            state["trace"].append({
                "stage": "answer_coverage",
                "explanation_present": bool(excerpts),
                "reason": "Verified figures alone do not answer the requested explanation",
            })
        return gaps, fallback_complete

    @staticmethod
    def _narrative_excerpt_score(passage: dict, category: str | None, base_score: float = 0.0) -> float:
        """Rank exact fallback passages by the requested narrative slot.

        The fallback is used precisely when the model did not synthesize an
        explanation, so a generic search score is too weak: it can select a
        cash-flow or foreign-exchange paragraph for a margin question. These
        lexical boosts are only for choosing source text; they never promote a
        passage into a causal claim.
        """
        text = str(passage.get("text") or "")
        folded = text.casefold()
        section = str(passage.get("section") or "").casefold()
        purposes = {str(value) for value in passage.get("purposes") or []}
        score = float(base_score)
        purpose_weights = {
            "经营变化原因": 18,
            "利润率原因": 22,
            "分部变化原因": 20,
            "现金与费用原因": 12,
            "整份申报扫描:原因": 22,
            "整份申报扫描:分部": 20,
            "监管与需求风险": 18,
            "供应与经营风险": 18,
            "客户集中风险": 22,
            "整份申报扫描:监管": 18,
            "整份申报扫描:需求": 18,
            "整份申报扫描:集中度": 22,
        }
        score += max(
            (weight for purpose, weight in purpose_weights.items() if purpose in purposes),
            default=0,
        )
        if category == "causality":
            direct = len(re.findall(
                r"primarily|mainly|due to|driven by|reflecting|attributed|came from|"
                r"increased|decreased|increase|decline|主要|由于|驱动|归因|增加|减少|下降",
                folded,
                re.I,
            ))
            topic = len(re.findall(
                r"revenue|net sales|gross margin|operating margin|operating income|"
                r"gross profit|cost of sales|operating expenses?|segment|费用|收入|利润率|"
                r"营业利润|毛利|销售额|分部",
                folded,
                re.I,
            ))
            score += direct * 4 + min(topic, 8) * 2
            if re.search(
                r"management|md&a|results of operations|operating results|经营|运营",
                section,
                re.I,
            ):
                score += 6
            if re.search(
                r"cash from operations|cash used in (?:financing|investing)|"
                r"现金流量|衍生工具|derivative",
                folded,
                re.I,
            ) and not re.search(
                r"gross margin|operating margin|operating income|cost of sales|"
                r"operating expenses?|毛利率|营业利润率|营业利润|费用",
                folded,
                re.I,
            ):
                score -= 18
            if re.search(r"risk factors?|market risk|风险因素|市场风险", section, re.I) and not re.search(
                r"operating income|operating margin|gross margin|营业利润|利润率",
                folded,
                re.I,
            ):
                score -= 10
        elif category == "risk":
            risk_hits = len(re.findall(
                r"risk|adverse|regulat|export|supply|customer|demand|compliance|"
                r"风险|监管|出口|供应|客户|需求|合规",
                folded,
                re.I,
            ))
            score += min(risk_hits, 12) * 3
            if re.search(r"risk factors?|business|风险因素|业务", section, re.I):
                score += 8
        elif category == "negation":
            denial_hits = len(re.findall(
                r"no material|not materially|did not|without material|"
                r"supply[- ]chain|demand|impact|没有重大|未造成重大|供应链|需求|影响",
                folded,
                re.I,
            ))
            score += min(denial_hits, 10) * 4
        return score

    @staticmethod
    def _observed_narrative_excerpt_fallback(state: AgentState) -> list[dict]:
        """Keep exact, scoped filing passages after a narrative model failure.

        This path is available only after the narrative prefetch scanned the
        requested filing and marked it as a supported candidate.  It retains a
        small, purpose-diverse set of source passages so a provider outage does
        not erase useful evidence or produce a fabricated conclusion.
        """
        context = state.get("narrative_context") or {}
        if context.get("answerability") != "supported_candidate":
            return []
        tools, focus = state["tools"], state.get("focus_ticker")
        filing_id = (context.get("filing") or {}).get("id")
        candidates: dict[str, tuple[int, float, dict]] = {}

        def add(passage: dict, score: float = 0.0) -> None:
            item_id = passage.get("id")
            if (
                not item_id
                or passage.get("kind", "passage") != "passage"
                or not passage.get("text")
                or (focus and passage.get("ticker") != focus)
                or (filing_id and passage.get("filing_id") != filing_id)
                or len(passage.get("text", "")) > 4000
            ):
                return
            canonical = tools.corpus.evidence(item_id, tools.as_of)
            if canonical is not None:
                passage = {
                    **canonical,
                    "purposes": passage.get("purposes") or canonical.get("purposes", []),
                }
            existing = tools.ledger.get(item_id)
            if existing is None or (
                passage.get("purposes") and not existing.get("purposes")
            ):
                tools.ledger[item_id] = passage
            score = ExecutionMixin._narrative_excerpt_score(
                passage, context.get("category"), score
            )
            purposes = passage.get("purposes") or []
            purpose_count = len(purposes) or 1
            current = candidates.get(item_id)
            if current is None or (purpose_count, score) > (current[0], current[1]):
                candidates[item_id] = (purpose_count, score, passage)

        # The filing-wide scan is purpose-labeled and therefore gets priority
        # over the broad query hits.  Query scores alone can rank an incidental
        # passage above the requested risk slot.
        for index, passage in enumerate(context.get("evidence", [])):
            add(passage, 0.5 - index / 1000.0)
        for trace in state.get("trace", []):
            if trace.get("tool") != "search_passages":
                continue
            for hit in (trace.get("result") or {}).get("hits", []):
                add(hit.get("passage") or {}, float(hit.get("score") or 0))
        if not candidates:
            return []

        limit = {"risk": 3, "causality": 3, "negation": 2}.get(
            context.get("category"), 2
        )
        ranked = sorted(
            candidates.values(),
            key=lambda value: (value[1], value[0], value[2].get("id", "")),
            reverse=True,
        )
        selected: list[dict] = []
        observed_purposes: set[str] = set()
        for _, _, passage in ranked:
            purposes = set(passage.get("purposes") or [])
            # Causal fallback passages are already scored for direct drivers,
            # margin terms and MD&A scope. Forcing purpose diversity there can
            # replace a second strong segment explanation with an unrelated
            # cash-flow or tax paragraph. Keep diversity for broad risk and
            # negation briefs, where separate slots are genuinely useful.
            if (
                context.get("category") != "causality"
                and selected
                and purposes
                and not purposes - observed_purposes
            ):
                continue
            selected.append(passage)
            observed_purposes.update(purposes)
            if len(selected) >= limit:
                break
        claims, _ = verify_submission(
            Submission(
                claims=[
                    Claim(kind="excerpt", evidence_ids=[passage["id"]], statement=passage["text"])
                    for passage in selected
                ]
            ),
            tools,
            state["question"],
            focus_ticker=focus,
            allow_excerpts=True,
        )
        return claims

    @staticmethod
    def _observed_module_excerpt_fallback(state: AgentState) -> list[dict]:
        """Retain exact source passages when a factual module cannot be drafted.

        This fallback is deliberately limited to factual sections. It never
        paraphrases or promotes a passage to a narrative claim; the page labels
        the result as original source material and keeps the normal evidence
        citation. Analysis modules continue to use their typed-evidence fallback
        and remain partial when interpretation is unavailable.
        """
        module = state.get("module")
        contract = module_contract(module) if module else None
        if contract is None or contract.kind != "factual":
            return []
        tools, focus = state["tools"], state.get("focus_ticker")
        keywords = {
            "ownership": ("common stock", "ordinary share", "nasdaq", "currency", "dollar", "depositary", "ads"),
            "audit": ("audited", "opinion", "consolidated financial statements", "report dated", "critical audit", "pcaob"),
        }.get(module, ())
        if not keywords:
            return []
        latest_filing = None
        filings = []
        for item in state.get("trace", []):
            if item.get("tool") != "list_filings":
                continue
            filings.extend((item.get("result") or {}).get("filings", []))
        if filings:
            scoped = [
                filing
                for filing in filings
                if not focus or filing.get("ticker") == focus
            ]
            annual = [
                filing
                for filing in scoped
                if filing.get("form", "").upper() in {"10-K", "20-F", "40-F"}
            ]
            latest_filing = max(
                annual or scoped,
                key=lambda filing: (filing.get("period_end", ""), filing.get("filed", "")),
                default=None,
            )
        candidates = []
        seen = set()
        for item in state.get("trace", []):
            if item.get("tool") != "search_passages":
                continue
            result = item.get("result") or {}
            for hit in result.get("hits", []):
                passage = hit.get("passage") or {}
                item_id = passage.get("id")
                text = passage.get("text", "")
                if (
                    not item_id
                    or item_id in seen
                    or not text
                    or passage.get("kind", "passage") != "passage"
                    or (focus and passage.get("ticker") != focus)
                ):
                    continue
                lower = text.lower()
                keyword_hits = sum(1 for keyword in keywords if keyword in lower)
                if not keyword_hits:
                    continue
                seen.add(item_id)
                if item_id not in tools.ledger:
                    tools.ledger[item_id] = passage
                latest = bool(latest_filing and passage.get("filing_id") == latest_filing.get("id"))
                candidates.append(
                    (
                        latest,
                        keyword_hits,
                        float(hit.get("score") or 0),
                        item_id,
                    )
                )
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        accepted = []
        for _, _, _, item_id in candidates[:2]:
            source = tools.ledger[item_id]
            checked, _ = verify_submission(
                Submission(
                    claims=[
                        Claim(
                            kind="excerpt",
                            evidence_ids=[item_id],
                            statement=source["text"],
                        )
                    ]
                ),
                tools,
                state["question"],
                focus_ticker=focus,
                allow_excerpts=True,
            )
            for detail in checked:
                detail["index"] = len(accepted)
                accepted.append(detail)
        return accepted

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
            fingerprint = None
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
                    cached = state.get("tool_cache", {}).get(fingerprint)
                    if cached is None:
                        raise ValueError("Duplicate call: use the previously returned evidence")
                    result = cached
                    state["trace"].append(
                        {
                            "step": len(state["trace"]) + 1,
                            "stage": "tool_cache_hit",
                            "tool": name,
                            "arguments": arguments,
                            "result": result,
                            "duration_ms": 0,
                        }
                    )
                    state["messages"].append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": model_context_json(
                                result,
                                seen_passage_ids=state.setdefault("context_passage_ids", set()),
                            ),
                        }
                    )
                    continue
                state["seen"].add(fingerprint)
                if wants_update_summary(state["question"]) and name != "submit_answer":
                    raise ValueError(
                        "Notice summary may only submit claims from supplied notice excerpts"
                    )
                if name == "submit_answer":
                    candidate = parse_submission(arguments)
                    # Models sometimes use a literal placeholder as the only
                    # gap while they are still deciding what to submit. It is
                    # not a meaningful disclosure and must follow the same
                    # bounded retry path as an empty payload.
                    meaningful_gaps = [
                        gap
                        for gap in candidate.gaps
                        if not _is_placeholder_gap(gap)
                    ]
                    if len(meaningful_gaps) != len(candidate.gaps):
                        candidate = Submission(claims=list(candidate.claims), gaps=meaningful_gaps)
                    # An empty tool call is not a completed abstention. Some
                    # providers emit ``claims=[]`` before they have finished
                    # reading the evidence; treating that call as terminal
                    # loses a supported answer. Give the model two bounded
                    # chances to turn the observed ledger into claims or a
                    # specific gap, while preserving the existing honest
                    # abstention after the limit.
                    if (
                        not candidate.claims
                        and not candidate.gaps
                        and int(state.get("empty_submission_attempts", 0)) < 2
                    ):
                        state["empty_submission_attempts"] = int(
                            state.get("empty_submission_attempts", 0)
                        ) + 1
                        submission = None
                        raise ValueError(
                            "Empty submission is not complete. Use the observed evidence "
                            "to submit supported claims, or put the specific missing part in gaps."
                        )
                    submission = candidate
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
            if name != "submit_answer" and fingerprint is not None:
                state.setdefault("tool_cache", {})[fingerprint] = result
            state["messages"].append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": model_context_json(
                        result,
                        seen_passage_ids=state.setdefault("context_passage_ids", set()),
                    ),
                }
            )
        if state.get("truncated_turn") and not submission:
            state["truncated_turn"] = False
            state["messages"].append(
                {
                    "role": "user",
                    "content": (
                        "The previous turn was truncated at the output limit. Stop "
                        "exploring and call submit_answer now: at most four short "
                        "claims built from the evidence already returned, and put "
                        "anything unsupported into gaps."
                    ),
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
        if not metrics and not ratios and compares_prior_year(question):
            # "Explain FY2022 growth" names no metric, yet completeness requires a
            # growth result. Use the metric the verified answer already presents:
            # that is the quantity the question is asking to grow, and the
            # existing period/unit guards still decide whether it can be computed.
            observed = {
                evidence_id
                for claim in state.get("verified", [])
                if claim.get("kind") == "fact"
                for evidence_id in claim.get("evidence_ids", [])
            }
            metrics = {
                tools.ledger[evidence_id].get("metric")
                for evidence_id in observed
                if evidence_id in tools.ledger
            } - {None}
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
            left_fact = _agreed_fact(facts.get(left, []))
            right_fact = _agreed_fact(facts.get(right, []))
            if left_fact is None or right_fact is None:
                # Competing values for the same metric and year stay a gap; a
                # duplicate observation that reports the same amount does not.
                continue
            ids = [left_fact["id"], right_fact["id"]]
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
        observations = set()
        separate_sources = bool(re.search(
            r"来源|版本|修订|重述|\b(?:sources?|filings|reports|revisions?|restat\w*|amend\w*)\b",
            question, re.I,
        ))
        for item in typed[:8]:
            claims = Submission(
                claims=[Claim(kind=item["kind"], evidence_ids=[item["id"]])], gaps=[]
            )
            verified, _ = verify_submission(claims, tools, question, focus_ticker=focus)
            if verified and item["kind"] == "fact" and not separate_sources:
                # A later filing may repeat the same observation. Collapse only
                # exact financial matches after verification, never rounded text
                # or differing restatements. All observed sources stay in ledger.
                observation = tuple(item.get(key) for key in (
                    "ticker", "metric", "concept", "label", "unit", "fiscal_year", "start", "end",
                )) + (Decimal(item["value"]),)
                if observation in observations:
                    continue
                observations.add(observation)
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
