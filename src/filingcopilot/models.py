from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .company_modules import ModuleKey


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filing(StrictModel):
    id: str
    ticker: str
    company: str
    cik: str
    accession: str
    form: str
    filed: date
    period_end: date
    source_url: str
    content_sha256: str


class Fact(StrictModel):
    id: str
    filing_id: str
    ticker: str
    metric: str
    label: str
    concept: str
    value: Decimal
    unit: str
    fiscal_year: int
    start: date | None = None
    end: date
    source_url: str
    locator: str


class StatementFact(StrictModel):
    """A row from a filed statement, including rows outside the metric registry.

    ``Fact`` remains the narrow, validated metric surface used by calculations.  This
    companion record preserves every statement row that can be identified from SEC
    XBRL, including an original label and any reporting dimensions.  Unknown rows are
    queryable as text and are never silently mapped to zero or to a registered metric.
    """

    id: str
    filing_id: str
    ticker: str
    statement: Literal["income", "balance", "cashflow", "other"]
    metric: str
    label: str
    concept: str
    value: Decimal
    unit: str
    fiscal_year: int
    start: date | None = None
    end: date
    source_url: str
    locator: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    dimension_labels: dict[str, str] = Field(default_factory=dict)


class Passage(StrictModel):
    id: str
    filing_id: str
    ticker: str
    section: str
    text: str
    source_url: str
    locator: str
    table_context: dict | None = None


class ExternalUpdate(StrictModel):
    """A dated external or HKEX item kept outside the SEC filing corpus.

    External material deliberately has its own evidence kind.  It can be cited
    as descriptive source text, but it never becomes a typed financial fact or
    an SEC filing event. HKEX announcements carry their own official exchange
    evidence level and language field.
    """

    id: str
    ticker: str = Field(pattern=r"^[A-Z0-9^][A-Z0-9.^=-]{0,14}$")
    source_type: Literal["transcript", "news", "hkex", "cninfo"]
    source_name: str = Field(min_length=1, max_length=160)
    evidence_level: Literal[
        "issuer_transcript", "issuer_news", "licensed_news", "public_news", "hkex_official", "cninfo_official"
    ]
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=20000)
    source_url: str = Field(pattern=r"^https://")
    source_feed_url: str = Field(pattern=r"^https://")
    source_config: dict
    published_at: datetime | None
    available_at: datetime
    publication_basis: Literal["source_timestamp", "first_observed"] = "source_timestamp"
    text_scope: Literal[
        "feed_excerpt", "transcript_excerpt", "hkex_announcement", "cninfo_announcement"
    ] = "feed_excerpt"
    language: Literal["en", "zh", "und"] = "und"
    event_date: date | None = None
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieved_at: datetime

    @model_validator(mode="after")
    def validate_temporal(self):
        if self.published_at and (self.published_at.tzinfo is None or self.published_at.utcoffset() is None):
            raise ValueError("External update publication time must include a timezone")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ValueError("External update availability must include a timezone")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("External update retrieval time must include a timezone")
        if self.publication_basis == "source_timestamp" and self.available_at != self.published_at:
            raise ValueError("Source-dated availability must match publication time")
        if self.publication_basis == "first_observed" and (self.published_at is not None or self.available_at != self.retrieved_at):
            raise ValueError("Undated material is available only at first retrieval, never at event time")
        if self.source_type == "transcript" and self.evidence_level != "issuer_transcript":
            raise ValueError("Transcripts require an explicit issuer transcript source")
        if self.source_type == "news" and self.evidence_level == "issuer_transcript":
            raise ValueError("News cannot use a transcript evidence level")
        if self.source_type == "news" and self.evidence_level not in ("issuer_news", "licensed_news", "public_news"):
            raise ValueError("News cannot claim an official exchange evidence level")
        if self.source_type == "hkex":
            if self.evidence_level != "hkex_official":
                raise ValueError("HKEX announcements require the hkex_official evidence level")
            if self.text_scope != "hkex_announcement":
                raise ValueError("HKEX announcements require the hkex_announcement text scope")
            if self.language not in ("en", "zh"):
                raise ValueError("HKEX announcements require an English or Chinese version")
        elif self.source_type == "cninfo":
            if self.evidence_level != "cninfo_official":
                raise ValueError("CNINFO announcements require the cninfo_official evidence level")
            if self.text_scope != "cninfo_announcement" or self.language != "zh":
                raise ValueError("CNINFO announcements require the Chinese announcement scope")
        elif self.language != "und":
            raise ValueError("Language is only recorded for HKEX announcements")
        self.published_at = self.published_at.astimezone(timezone.utc) if self.published_at else None
        self.available_at = self.available_at.astimezone(timezone.utc)
        self.retrieved_at = self.retrieved_at.astimezone(timezone.utc)
        return self

    @property
    def available_day(self) -> date:
        """Knowledge-date controls use the source market's disclosure calendar."""
        zone = timezone(timedelta(hours=8)) if self.source_type in ("hkex", "cninfo") else timezone.utc
        return self.available_at.astimezone(zone).date()


class Calculation(StrictModel):
    id: str
    operation: Literal[
        "growth",
        "ratio",
        "difference",
        "annual_pe",
        "provider_ttm_pe",
        "free_cash_flow",
        "net_cash_capex",
        "trailing_sum",
    ]
    input_ids: list[str]
    value: Decimal
    unit: str
    label: str
    formula: str
    ticker: str
    fiscal_year: int


class Claim(StrictModel):
    kind: Literal["fact", "calculation", "market", "valuation", "narrative", "excerpt", "analysis"]
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    # Numeric sentences are rendered by code. The model cannot supply their wording or values.
    statement: str = Field(default="", max_length=4000)


class Submission(StrictModel):
    claims: list[Claim] = Field(default_factory=list, max_length=10)
    gaps: list[str] = Field(default_factory=list, max_length=8)


class ResearchRequest(StrictModel):
    question: str = Field(min_length=2, max_length=1500)
    ticker: str | None = Field(default=None, max_length=10)
    as_of: date = Field(default_factory=date.today)
    mode: Literal["agent", "reference"] = "agent"
    company: str | None = Field(default=None, min_length=1, max_length=120)
    auto_acquire: bool = False
    depth: Literal["brief", "full"] = "brief"
    language: Literal["zh", "en"] = "en"
    # Company-research section summaries declare their module; absence keeps
    # every existing request behaviour unchanged.
    module: ModuleKey | None = None


class FollowupRequest(StrictModel):
    research_id: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=500)
    as_of: date | None = None
    mode: Literal["agent", "reference"] = "reference"
    refresh_sources: bool = False
    language: Literal["zh", "en"] = "en"


class TranslationRequest(StrictModel):
    texts: list[str] = Field(min_length=1, max_length=30)
    target: Literal["zh"] = "zh"


class CompanyLookup(StrictModel):
    query: str = Field(min_length=1, max_length=120)
    translate: bool = True


class CompanyPreparation(StrictModel):
    ticker: str = Field(min_length=1, max_length=10)
    as_of: date = Field(default_factory=date.today)
    years: int = Field(default=2, ge=1, le=3)


class ResearchAnswer(StrictModel):
    id: str
    question: str
    as_of: date
    mode: str
    status: Literal["answered", "partial", "abstained", "error"]
    # Added after the original status enum.  Keep a default so old research
    # packages remain readable while new runs explain *why* a result is partial.
    status_reason: Literal[
        "complete",
        "budget_expired",
        "cancelled",
        "source_or_model_error",
        "review_incomplete",
        "evidence_gap",
        "no_supported_evidence",
        "reused_evidence",
    ] = "complete"
    answer: list[dict]
    gaps: list[str]
    evidence: list[dict]
    trace: list[dict]
    usage: dict
    elapsed_ms: int
    model: str | None = None
    # Packages created before language metadata used Chinese; keep legacy decoding stable.
    language: Literal["zh", "en"] = "zh"
    research_type: Literal["filing", "stock"] = "filing"
    contrarian: dict = Field(
        default_factory=lambda: {
            "status": "not_run",
            "query": None,
            "items": [],
            "note": "本轮未执行反证检索。",
        }
    )
    followup: dict | None = None

    @model_validator(mode="after")
    def infer_legacy_status_reason(self):
        # A package written before this field existed has the default
        # ``complete`` even when its status is partial/abstained.  Give such
        # records a conservative fallback; the service later refines it from
        # the full execution receipt.
        if self.status != "answered" and self.status_reason == "complete":
            self.status_reason = (
                "evidence_gap" if self.answer else "no_supported_evidence"
            )
        return self
