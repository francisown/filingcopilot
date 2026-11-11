"""Pydantic contracts for the monitoring surface.

These objects are deliberately explicit. A brief, an event and a case are all
persisted as JSON so a later reviewer can replay exactly what the analyst saw.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..models import StrictModel
from .taxonomy import (
    DEFAULT_FORMS,
    SUPPORTED_FORMS,
    WATCH_ITEMS,
    normalise_form,
    watch_items,
)


class WatchPolicy(StrictModel):
    ticker: str = Field(min_length=1, max_length=15)
    watch: list[str] = Field(default_factory=lambda: list(watch_items(None)))
    forms: list[str] = Field(default_factory=lambda: list(DEFAULT_FORMS))
    enabled: bool = True
    materiality_floor: Literal["low", "medium", "high"] = "medium"
    notify: bool = True
    version: int = Field(default=1, ge=1)

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        return str(value).strip().upper()

    @field_validator("watch")
    @classmethod
    def _watch(cls, value: list[str]) -> list[str]:
        return watch_items(value)

    @field_validator("forms")
    @classmethod
    def _forms(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value or DEFAULT_FORMS:
            form = normalise_form(item)
            if form not in SUPPORTED_FORMS:
                raise ValueError(f"Unsupported form: {item}")
            if form not in result:
                result.append(form)
        return result or list(DEFAULT_FORMS)


class FactDelta(StrictModel):
    metric: str
    label: str = ""
    current_value: str
    prior_value: str | None = None
    unit: str = ""
    change_pct: float | None = None
    change_abs: str | None = None
    change_pp: float | None = None
    comparison_basis: str = "prior_period"
    current_evidence_id: str | None = None
    prior_evidence_id: str | None = None
    materiality: Literal["high", "medium", "low", "suppressed"] = "low"


class SectionDelta(StrictModel):
    section: str
    similarity: float = 1.0
    added_chars: int = 0
    removed_chars: int = 0
    excerpt: str = ""
    char_start: int | None = None
    char_end: int | None = None
    char_text: str = ""
    section_text: str = ""
    quote_hash: str = ""
    semantic_summary: str = ""
    summary_kind: Literal["excerpt", "model"] = "excerpt"
    evidence_ids: list[str] = Field(default_factory=list)
    materiality: Literal["high", "medium", "low", "suppressed"] = "low"


class EvidenceRef(StrictModel):
    id: str
    kind: str = "passage"
    label: str = ""
    quote: str = ""
    section: str = ""
    page: int | None = None
    locator: str = ""
    source_url: str = ""
    filing_id: str = ""
    char_start: int | None = None
    char_end: int | None = None
    quote_hash: str = ""


class Inference(StrictModel):
    text: str
    basis_evidence_ids: list[str] = Field(default_factory=list)
    kind: Literal["inference"] = "inference"


class DisclosureEvent(StrictModel):
    id: str = ""
    event_key: str = ""
    ticker: str
    filing_id: str
    prior_filing_id: str | None = None
    amends_filing_id: str | None = None
    superseded_by_event_id: str | None = None
    event_type: str
    subtype: str = ""
    label: str = ""
    tags: list[str] = Field(default_factory=list)
    materiality: Literal["high", "medium", "low", "suppressed"] = "medium"
    materiality_score: int = 50
    materiality_inputs: dict = Field(default_factory=dict)
    materiality_rule_hits: list[str] = Field(default_factory=list)
    comparison_basis: str = "first_observation"
    facts: list[FactDelta] = Field(default_factory=list)
    section_deltas: list[SectionDelta] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    inferences: list[Inference] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    status: Literal["open", "superseded"] = "open"
    created_at: str = ""
    updated_at: str = ""
    taxonomy_version: str = "monitor-v1"
    extractor_version: str = "monitor-v1"


class Brief(StrictModel):
    case_id: str = ""
    ticker: str
    company: str = ""
    filing_id: str
    form: str
    filed: str
    period_end: str
    headline: str
    what_happened: list[str] = Field(default_factory=list)
    numbers: list[FactDelta] = Field(default_factory=list)
    risks: list[SectionDelta] = Field(default_factory=list)
    why_matters: list[Inference] = Field(default_factory=list)
    sources: list[EvidenceRef] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    events: list[DisclosureEvent] = Field(default_factory=list)
    generated_at: str = ""
    generator: Literal["template", "model"] = "template"
    taxonomy_version: str = "monitor-v1"


class Case(StrictModel):
    id: str = ""
    ticker: str
    filing_id: str
    form: str = ""
    filed: str = ""
    period_end: str = ""
    status: Literal[
        "pending_review",
        "approved",
        "edited",
        "dismissed",
        "needs_followup",
    ] = "pending_review"
    priority: Literal["high", "medium", "low"] = "medium"
    title: str = ""
    assignee: str | None = None
    brief: Brief | None = None
    # Each regenerated brief gets a new version. Approval binds to a version,
    # so a later regeneration cannot silently preserve an old approval.
    brief_version: int = Field(default=1, ge=1)
    # Monotonic state revision. Every review/regeneration increments it, even
    # when the brief version does not change (for example approve v1).
    revision: int = Field(default=1, ge=1)
    approved_brief: Brief | None = None
    approved_version: int | None = None
    events: list[DisclosureEvent] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    suppressed_reason: str | None = None


class CaseReview(StrictModel):
    id: str = ""
    case_id: str
    action: Literal["approve", "dismiss", "edit", "feedback", "reopen", "regenerate"]
    actor: str = "local"
    reason_code: str = ""
    comment: str = ""
    edit_diff: str = ""
    brief_version: int = 0
    created_at: str = ""


class ScanRequest(StrictModel):
    tickers: list[str] | None = None
    as_of: date | None = None
    forms: list[str] | None = None
    bootstrap: bool = True
    generate_brief: bool = True
    # Rebuild snapshots/events/briefs even when a case already exists. The
    # analyst review status is preserved.
    reprocess: bool = False
    # None inherits FC_MONITORING_USE_MODEL; an explicit true/false overrides it.
    use_model: bool | None = None

    @field_validator("tickers")
    @classmethod
    def _tickers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for item in value:
            ticker = str(item).strip().upper()
            if ticker and ticker not in result:
                result.append(ticker)
        if len(result) > 30:
            raise ValueError("A scan supports at most 30 tickers")
        return result

    @field_validator("forms")
    @classmethod
    def _forms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for item in value:
            form = normalise_form(item)
            if form not in SUPPORTED_FORMS:
                raise ValueError(f"Unsupported form: {item}")
            if form not in result:
                result.append(form)
        return result


class ScanResult(StrictModel):
    run_id: str
    as_of: str
    tickers: list[str]
    filings_seen: int = 0
    new_filings: int = 0
    cases_created: int = 0
    events_created: int = 0
    suppressed: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class ReplayRequest(StrictModel):
    tickers: list[str] | None = None
    start: date
    end: date
    forms: list[str] | None = None
    generate_brief: bool = True
    use_model: bool | None = None

    @field_validator("tickers")
    @classmethod
    def _tickers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for item in value:
            ticker = str(item).strip().upper()
            if ticker and ticker not in result:
                result.append(ticker)
        if len(result) > 30:
            raise ValueError("A replay supports at most 30 tickers")
        return result

    @field_validator("end")
    @classmethod
    def _end(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Replay end date cannot be later than today")
        return value

    @field_validator("start")
    @classmethod
    def _start(cls, value: date) -> date:
        return value

    @model_validator(mode="after")
    def _range(self):
        if self.start > self.end:
            raise ValueError("Replay start must not be later than end")
        if (self.end - self.start).days > 729:
            # 730 calendar days inclusive means end - start <= 729.
            raise ValueError("Replay window supports at most 730 calendar days")
        return self


class ReplayResult(StrictModel):
    run_id: str
    start: str
    end: str
    tickers: list[str]
    filings_seen: int = 0
    cases_created: int = 0
    events_created: int = 0
    suppressed: int = 0
    warnings: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class ReviewRequest(StrictModel):
    action: Literal["approve", "dismiss", "edit", "feedback", "reopen"]
    actor: str = Field(default="local", max_length=120)
    reason_code: str = Field(default="", max_length=80)
    comment: str = Field(default="", max_length=2000)
    # Required for approve/dismiss/edit: the reviewer is approving a specific
    # generated version, not whichever brief happens to be current later.
    brief_version: int | None = Field(default=None, ge=1)
    brief: Brief | None = None


class DeliveryRequest(StrictModel):
    channel: Literal["local", "webhook"] = "local"


class DigestRequest(StrictModel):
    start: date | None = None
    end: date | None = None
    channel: Literal["local", "webhook", "slack", "smtp"] = "local"
    force: bool = False


class PolicyPayload(StrictModel):
    """A policy update may omit fields; the service fills defaults."""

    ticker: str = Field(min_length=1, max_length=15)
    watch: list[str] | None = None
    forms: list[str] | None = None
    enabled: bool | None = None
    materiality_floor: Literal["low", "medium", "high"] | None = None
    notify: bool | None = None

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        return str(value).strip().upper()

    @field_validator("watch")
    @classmethod
    def _watch(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result = watch_items(value)
        invalid = [item for item in value if item not in WATCH_ITEMS]
        if invalid:
            raise ValueError(f"Unsupported watch item: {invalid[0]}")
        return result

    @field_validator("forms")
    @classmethod
    def _forms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for item in value:
            form = normalise_form(item)
            if form not in SUPPORTED_FORMS:
                raise ValueError(f"Unsupported form: {item}")
            if form not in result:
                result.append(form)
        return result
