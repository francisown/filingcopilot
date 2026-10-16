from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import TYPE_CHECKING

from .execution import checkpoint, progress, stopped
from .fundamentals import is_stock_question
from .localization import state_language
from .model_context import model_context_json
from .models import Submission
from .quarterly import covers_requested_quarter, stock_request_scope
from .verification import (
    REVIEW_SYSTEM,
    REVIEW_TOOL,
    ReviewBatch,
    parse_review,
    verify_submission,
)

if TYPE_CHECKING:
    from .agent import AgentState


class ReviewMixin:
    @staticmethod
    def _review_key(state: AgentState, claim: dict, *, include_statement: bool = True) -> str:
        # Reuse applies only within this investigation, and binds the complete inputs.
        payload = {
            "question": state["question"],
            "review_policy": REVIEW_SYSTEM,
            "as_of": state["tools"].as_of.isoformat(),
            "domain": state["tools"].domain.model_dump(mode="json"),
            "claim": {
                "kind": claim["kind"],
                **({"statement": claim["statement"]} if include_statement else {}),
                "evidence_ids": claim["evidence_ids"]
                if include_statement
                else sorted(set(claim["evidence_ids"])),
            },
            "evidence": [
                state["tools"].ledger[i]
                for i in (
                    claim["evidence_ids"]
                    if include_statement
                    else sorted(set(claim["evidence_ids"]))
                )
            ],
        }
        return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _retain_verified_partial(
        self, state: AgentState, verified: list, *, incomplete: bool | None = None
    ) -> list:
        candidate = state.get("verified_candidate")
        analysis_count = sum(c["kind"] == "analysis" for c in verified)
        if incomplete is None:
            incomplete = bool(state.get("errors"))
        if (
            candidate is None
            or analysis_count
            and (
                not incomplete
                or sum(c.kind == "analysis" for c in candidate.claims) <= analysis_count
            )
            or (stopped() and verified)
        ):
            return verified

        def cached_review(question, claims):
            return {
                "verdicts": [
                    {
                        **(
                            {
                                "supported": False,
                                "reason": "Later independent rejection of the same evidence basis; previous approval needs fresh review",
                            }
                            if self._review_key(state, c, include_statement=False)
                            in state.get("review_conflicts", set())
                            else state.get("review_cache", {}).get(
                                self._review_key(state, c),
                                {
                                    "supported": False,
                                    "reason": "Previous review inputs changed; claim withheld",
                                },
                            )
                        ),
                        "index": c["index"],
                    }
                    for c in claims
                ]
            }

        retained, _ = verify_submission(
            candidate, state["tools"], state["question"], cached_review, state["focus_ticker"]
        )
        if not retained or (not stopped() and not any(c["kind"] == "analysis" for c in retained)):
            return verified
        if analysis_count and sum(c["kind"] == "analysis" for c in retained) <= analysis_count:
            return verified
        # Deliver one coherent earlier draft, never merge conflicting rewritten drafts.
        state["retained_gaps"] = list(candidate.gaps)
        state["trace"].append(
            {
                "step": len(state["trace"]) + 1,
                "stage": "retained_verified_partial",
                "accepted_claims": len(retained),
                "reason": "Research stopped; previous claims rechecked against unchanged evidence"
                if stopped()
                else "Incomplete later draft lost analysis coverage; coherent previous claims rechecked against unchanged evidence",
            }
        )
        return retained

    @state_language
    def verify(self, state: AgentState) -> dict:
        progress("verify", "正在核对数值、期间、证据归属与叙述支持。")
        cache = state.setdefault("review_cache", {})
        conflicts = state.setdefault("review_conflicts", set())

        def semantic_review(question, narratives):
            verdicts, pending = [], []
            for claim in narratives:
                key = self._review_key(state, claim)
                if (
                    key in cache
                    and self._review_key(state, claim, include_statement=False) not in conflicts
                ):
                    verdict = {**cache[key], "index": claim["index"]}
                    verdicts.append(verdict)
                    state["trace"].append(
                        {
                            "step": len(state["trace"]) + 1,
                            "stage": "semantic_review_cached",
                            "claim_indexes": [claim["index"]],
                            "verdicts": [verdict],
                            "input_sha256": key,
                        }
                    )
                else:
                    pending.append(claim)
            batch_size = max(1, min(8, self.settings.review_batch_size))
            for offset in range(0, len(pending), batch_size):
                claims, evidence = [], {}
                for claim in pending[offset : offset + batch_size]:
                    claims.append(
                        {k: claim[k] for k in ("index", "kind", "statement", "evidence_ids")}
                    )
                    for key, item in zip(claim["evidence_ids"], claim["evidence"], strict=True):
                        evidence[key] = {
                            "kind": item["kind"],
                            "ticker": item["ticker"],
                            "text": item["text"],
                            "comparison_periods": item["comparison_periods"],
                            "filings": [
                                {k: f[k] for k in ("ticker", "form", "filed", "period_end")}
                                for f in item["filings"]
                            ],
                        }
                indexes = [c["index"] for c in claims]
                review_tool = deepcopy(REVIEW_TOOL)
                verdict_schema = review_tool["function"]["parameters"]["properties"]["verdicts"]
                verdict_schema["minItems"] = verdict_schema["maxItems"] = len(indexes)
                verdict_schema["items"]["properties"]["index"]["enum"] = indexes
                try:
                    checkpoint()
                    progress("semantic_review", "正在独立审查叙述与分析。", claim_count=len(claims))
                    result = self.gateway.complete(
                        [
                            {"role": "system", "content": REVIEW_SYSTEM},
                            {
                                "role": "user",
                                "content": model_context_json(
                                    {
                                        "question": question,
                                        "claims": claims,
                                        "evidence": evidence,
                                        "required_indexes": indexes,
                                        "instruction": "Return each supplied index exactly once, without renumbering the claims.",
                                    }
                                ),
                            },
                        ],
                        [review_tool],
                        review=True,
                    )
                    self._record_model(state, result, "semantic_review")
                    calls = result["message"].get("tool_calls")
                    if calls:
                        if len(calls) != 1 or calls[0]["function"]["name"] != "submit_review":
                            raise ValueError("Reviewer must call submit_review exactly once")
                        payload = json.loads(calls[0]["function"]["arguments"])
                    else:
                        # Accept providers that deliver the same validated JSON in content.
                        payload = parse_review(result["message"].get("content") or "")
                    batch = ReviewBatch.model_validate(payload)
                    if sorted(v.index for v in batch.verdicts) != sorted(indexes):
                        raise ValueError("Reviewer must return this batch's indexes exactly once")
                    parsed = batch.model_dump()
                    state["trace"][-1].update(claim_indexes=indexes, verdicts=parsed["verdicts"])
                    for claim in claims:
                        verdict = next(
                            v for v in parsed["verdicts"] if v["index"] == claim["index"]
                        )
                        if verdict["supported"]:
                            cache[self._review_key(state, claim)] = verdict
                        else:
                            # Rewording a claim must not let an earlier approval of this
                            # exact evidence basis outlive an explicit later rejection.
                            # Network/malformed review failures do not enter this set.
                            conflicts.add(self._review_key(state, claim, include_statement=False))
                    verdicts.extend(parsed["verdicts"])
                except Exception as exc:
                    # A malformed or unavailable batch cannot erase independently
                    # reviewed claims in other batches, nor approve unreviewed claims.
                    failed = [
                        {
                            "index": index,
                            "supported": False,
                            "reason": "Independent review unavailable for this batch; claim withheld",
                        }
                        for index in indexes
                    ]
                    state["trace"].append(
                        {
                            "step": len(state["trace"]) + 1,
                            "stage": "semantic_review_error",
                            "claim_indexes": indexes,
                            "error_type": type(exc).__name__,
                            "retries": getattr(exc, "gateway_retries", []),
                            "verdicts": failed,
                        }
                    )
                    verdicts.extend(failed)
            return {"verdicts": verdicts}

        verified, errors = verify_submission(
            state["submission"],
            state["tools"],
            state["question"],
            semantic_review,
            state["focus_ticker"],
        )
        scope = state.get("requested_scope", {})
        if (
            scope.get("primary") == "quarter"
            and scope.get("period_end")
            and not covers_requested_quarter(verified, state["tools"], scope["period_end"])
        ):
            errors.append(
                "The requested latest single-quarter operating assessment is still missing. "
                "Use a narrow quarterly_overview comparison with its exact observed IDs; "
                "annual or YTD analysis cannot replace it. If unavailable, disclose the specific gap."
            )
        if any(c["kind"] == "analysis" for c in verified) or (
            verified and state.get("verified_candidate") is None
        ):
            candidate = Submission(
                claims=[
                    state["submission"].claims[c["index"]].model_copy(deep=True) for c in verified
                ],
                gaps=list(state["submission"].gaps),
            )
            previous = state.get("verified_candidate")
            if (
                previous is None
                or not errors
                or sum(c.kind == "analysis" for c in candidate.claims)
                >= sum(c.kind == "analysis" for c in previous.claims)
            ):
                state["verified_candidate"] = candidate
        if (
            is_stock_question(state["question"])
            and verified
            and not any(c["kind"] == "analysis" for c in verified)
        ):
            errors.append(
                "The stock question needs an evidence-grounded assessment, not only a list of figures. Add qualitative analysis of observed growth, profitability or cash generation, and the valuation condition."
            )
        state["trace"].append(
            {
                "step": len(state["trace"]) + 1,
                "stage": "verify",
                "accepted_claims": len(verified),
                "findings": errors,
            }
        )
        progress(
            "verified",
            "本轮证据核验已完成。",
            accepted_claims=len(verified),
            withheld_claims=len(state["submission"].claims) - len(verified),
        )
        if (
            not stopped()
            and errors
            and state["repairs"] < 2
            and state["rounds"] < state.get("round_limit", self.settings.max_rounds)
        ):
            state["messages"].append(
                {
                    "role": "user",
                    "content": model_context_json(
                        {
                            "type": "verification_feedback",
                            "question": state["question"],
                            "requested_scope": state.get("requested_scope")
                            or stock_request_scope(state["question"]),
                            "findings": errors,
                            "approved_claims": [
                                state["submission"].claims[c["index"]].model_dump(mode="json")
                                for c in verified
                            ],
                            "rejected_claims": [
                                {"index": i, "claim": c.model_dump(mode="json")}
                                for i, c in enumerate(state["submission"].claims)
                                if i not in {v["index"] for v in verified}
                            ],
                            "instruction": "Preserve approved claims verbatim, including their evidence_ids. "
                            "Repair only rejected claims with the required observed evidence, or remove "
                            "them and disclose the specific gap. The leading assessment must address "
                            "the requested scope. Submit a complete coherent draft with approved and "
                            "repaired claims together; do not add claim IDs or other fields outside the "
                            "submit_answer schema. All claims will still be verified.",
                        }
                    ),
                }
            )
            return {
                **state,
                "verified": verified,
                "errors": errors,
                "repairs": state["repairs"] + 1,
                "action": "reason",
            }
        verified = self._retain_verified_partial(state, verified, incomplete=bool(errors))
        return {**state, "verified": verified, "errors": errors, "action": "end"}
