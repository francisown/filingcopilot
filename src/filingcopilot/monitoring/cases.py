"""Review-case state machine, audit trail and analyst feedback."""

from __future__ import annotations

import difflib
import hashlib
import json
import uuid
from datetime import datetime, timezone

from .models import Brief, Case, CaseReview, DisclosureEvent, ReviewRequest
from .taxonomy import MATERIALITY_RANK


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CaseManager:
    def __init__(self, store):
        self.store = store

    def ensure(
        self,
        filing: dict,
        events: list[DisclosureEvent],
        brief: Brief | None,
        *,
        refresh: bool = False,
    ) -> Case:
        existing = self.store.get_case_by_filing(filing["id"])
        if existing is not None:
            if not refresh:
                return existing
            def brief_key(value: Brief | None) -> str:
                if value is None:
                    return ""
                # generated_at is a run timestamp, not reviewable content; it
                # must not create a new version on its own.
                return json.dumps(
                    value.model_dump(exclude={"generated_at", "case_id"}),
                    ensure_ascii=False,
                    sort_keys=True,
                )

            old_key = brief_key(existing.brief)
            new_key = brief_key(brief)
            changed = bool(brief is not None and new_key != old_key)
            approved_brief = existing.approved_brief
            approved_version = existing.approved_version
            if changed and existing.status in {"approved", "edited"} and approved_brief is None:
                approved_brief = existing.brief
                approved_version = existing.brief_version
            priority = (
                max(
                    (event.materiality for event in events),
                    key=lambda value: MATERIALITY_RANK.get(value, 0),
                )
                if events
                else existing.priority
            )
            update = {
                "events": events,
                "priority": priority,
                "updated_at": _now(),
            }
            if brief is not None:
                update["brief"] = brief
                update["title"] = brief.headline
            if changed:
                update.update(
                    {
                        "brief_version": existing.brief_version + 1,
                        "approved_brief": approved_brief,
                        "approved_version": approved_version,
                        "status": "pending_review",
                    }
                )
            update["revision"] = existing.revision + 1
            updated = existing.model_copy(update=update)
            if not changed:
                try:
                    return self.store.update_case_if_revision(
                        updated, existing.revision
                    )
                except ValueError:
                    return self.store.get_case(existing.id) or updated
            old = (
                json.dumps(
                    existing.brief.model_dump(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).splitlines()
                if existing.brief is not None
                else []
            )
            new = json.dumps(
                brief.model_dump(), ensure_ascii=False, indent=2, sort_keys=True
            ).splitlines()
            diff = "\n".join(
                difflib.unified_diff(
                    old, new, fromfile="brief-before", tofile="brief-after", lineterm=""
                )
            )
            review = CaseReview(
                id="review-" + uuid.uuid4().hex,
                case_id=existing.id,
                action="regenerate",
                actor="system",
                reason_code="content_changed",
                comment="简报内容已重新生成；原批准版本保留。",
                edit_diff=diff,
                brief_version=updated.brief_version,
                created_at=_now(),
            )
            try:
                return self.store.update_case_if_revision(
                    updated,
                    existing.revision,
                    review=review,
                    audit_actor="system",
                    audit_action="regenerate",
                    audit_before=existing.model_dump_json(),
                    audit_after=updated.model_dump_json(),
                )
            except ValueError:
                # Another process won the version race. Never overwrite it.
                return self.store.get_case(existing.id) or updated
        priority = "low"
        for event in events:
            if MATERIALITY_RANK.get(event.materiality, 0) > MATERIALITY_RANK.get(priority, 0):
                priority = event.materiality
        if priority not in {"high", "medium", "low"}:
            priority = "low"
        title = (
            brief.headline
            if brief is not None
            else f"{filing['ticker']} {filing['form']}：{events[0].label if events else '申报更新'}"
        )
        key = hashlib.sha256(f"{filing['id']}|monitor-case".encode()).hexdigest()[:20]
        case = Case(
            id="case-" + key,
            ticker=filing["ticker"],
            filing_id=filing["id"],
            form=filing["form"],
            filed=filing["filed"],
            period_end=filing["period_end"],
            status="pending_review",
            priority=priority,
            title=title,
            brief=brief,
            events=events,
            created_at=_now(),
            updated_at=_now(),
        )
        return self.store.create_case(case)

    def get(self, case_id: str) -> Case:
        case = self.store.get_case(case_id)
        if case is None:
            raise ValueError("案件不存在。")
        return case

    def list(self, *, status: str | None = None, ticker: str | None = None, limit: int = 100) -> list[Case]:
        return self.store.list_cases(status=status, ticker=ticker, limit=limit)

    def reviews(self, case_id: str) -> list[CaseReview]:
        return self.store.list_reviews(case_id)

    def review(self, case_id: str, request: ReviewRequest) -> Case:
        case = self.get(case_id)
        before = case.model_dump_json()
        action = request.action
        if action in {"approve", "dismiss", "edit"}:
            if request.brief_version is None:
                raise ValueError("审核动作需要 brief_version；请刷新案件后重试。")
            if request.brief_version != case.brief_version:
                raise ValueError(
                    "简报已重新生成，当前版本与审核版本不一致；请刷新后重新审核。"
                )
        if action == "feedback" and not (request.comment or request.reason_code):
            raise ValueError("反馈需要 reason_code 或 comment。")
        if action == "edit" and request.brief is None:
            raise ValueError("编辑需要提交完整 brief。")
        if action == "reopen" and not (request.comment or request.reason_code):
            raise ValueError("重新打开需要原因。")

        update: dict = {
            "updated_at": _now(),
            "revision": case.revision + 1,
        }
        edit_diff = ""
        if action == "approve":
            update["status"] = "approved"
            update["approved_brief"] = request.brief or case.brief
            update["approved_version"] = case.brief_version
        elif action == "dismiss":
            update["status"] = "dismissed"
        elif action == "edit":
            update["status"] = "edited"
            update["brief"] = request.brief
            update["brief_version"] = case.brief_version + 1
            update["approved_brief"] = request.brief
            update["approved_version"] = case.brief_version + 1
            if case.brief is not None and request.brief is not None:
                old = json.dumps(
                    case.brief.model_dump(), ensure_ascii=False, indent=2, sort_keys=True
                ).splitlines()
                new = json.dumps(
                    request.brief.model_dump(), ensure_ascii=False, indent=2, sort_keys=True
                ).splitlines()
                edit_diff = "\n".join(
                    difflib.unified_diff(
                        old,
                        new,
                        fromfile="brief-before",
                        tofile="brief-after",
                        lineterm="",
                    )
                )
        elif action == "reopen":
            update["status"] = "pending_review"
        # feedback deliberately keeps the current status

        edited = case.model_copy(update=update)

        review = CaseReview(
            id="review-" + uuid.uuid4().hex,
            case_id=case_id,
            action=action,
            actor=request.actor or "local",
            reason_code=request.reason_code,
            comment=request.comment,
            edit_diff=edit_diff,
            brief_version=edited.brief_version,
            created_at=_now(),
        )
        # The version check, case update, review and audit are one transaction.
        # A review built from v1 cannot overwrite a v2 generated concurrently.
        return self.store.update_case_if_revision(
            edited,
            case.revision,
            review=review,
            audit_actor=request.actor or "local",
            audit_action=action,
            audit_before=before,
            audit_after=edited.model_dump_json(),
        )
