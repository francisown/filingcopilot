"""Deterministic event extraction and materiality assignment.

No model is used here. 8-K item codes, XBRL fact deltas and targeted section
changes are sufficient to produce a reviewable event set for v1.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .models import DisclosureEvent, FactDelta, Inference, SectionDelta
from .taxonomy import (
    EVENT_TYPES,
    EXTRACTOR_VERSION,
    MATERIALITY_RANK,
    TAXONOMY_VERSION,
    classify_8k,
    event_types_for_watch,
    fact_materiality,
    form_root,
    max_materiality,
)

ITEM_KEYWORDS = {
    "1.01": ("material definitive agreement", "credit agreement", "indenture", "agreement"),
    "1.02": ("termination", "terminated", "material definitive agreement"),
    "1.03": ("bankruptcy", "receivership"),
    "1.05": ("cybersecurity", "cyber", "security incident"),
    "2.01": ("acquisition", "disposition", "completed"),
    "2.02": ("results of operations", "financial condition", "earnings", "revenue", "net income"),
    "2.03": ("direct financial obligation", "debt", "credit facility", "note"),
    "2.04": ("accelerate", "acceleration", "financial obligation"),
    "2.05": ("exit", "disposal", "restructuring"),
    "2.06": ("impairment", "write-down", "write down"),
    "3.01": ("delisting", "listing"),
    "4.02": ("non-reliance", "restatement", "previously issued"),
    "5.01": ("change in control", "control"),
    "5.02": ("director", "officer", "resign", "appoint", "departure"),
    "5.03": ("bylaws", "articles"),
    "7.01": ("regulation fd", "reg fd"),
    "8.01": ("other events",),
}

GUIDANCE_POSITIVE = re.compile(
    r"\b(expect|expects|anticipate|anticipates|forecast|forecasts|project|projects|outlook|guidance)\b",
    re.I,
)
GUIDANCE_NEGATIVE = re.compile(
    r"\b(do not|don't|does not|not|no longer|without)\b[^.]{0,60}"
    r"\b(provide|provide[sd]?|give|issue|offer|reaffirm|update)\b[^.]{0,40}"
    r"\b(guidance|outlook|forecast)\b",
    re.I,
)
GUIDANCE_NUMERIC = re.compile(
    r"(\$|\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*(?:billion|million|bn|m)\b|"
    r"\bQ[1-4]\b|\bFY\s*\d{2,4}\b)",
    re.I,
)
GUIDANCE_DISCLAIMER = re.compile(
    r"(forward-looking statements?|actual results? (?:may|could) differ|"
    r"no assurance|undue reliance|not undertake)",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_key(ticker: str, filing_id: str, event_type: str, subtype: str) -> str:
    value = f"{ticker}|{filing_id}|{event_type}|{subtype}"
    return hashlib.sha256(value.encode()).hexdigest()[:24]


class EventBuilder:
    def __init__(self, service, store):
        self.service = service
        self.store = store

    def _passages(self, filing: dict):
        return self.service.corpus.passages(
            self.service.today, filing["ticker"], filing_id=filing["id"]
        )

    def _summary(self, passages, limit: int = 320) -> str:
        for passage in passages:
            text = " ".join(str(passage.text).split())
            if len(text) >= 40:
                return text[: limit - 1] + "…" if len(text) > limit else text
        return ""

    def _evidence_ids(self, passages, limit: int = 5) -> list[str]:
        return [item.id for item in passages[:limit]]

    def _all_passages(self, filing: dict) -> list:
        passages = list(self._passages(filing))
        accession = filing.get("accession")
        if not accession:
            return passages
        catalog = self.service.corpus.catalog(
            self.service.today, filing["ticker"]
        )
        for item in catalog:
            if (
                item.get("accession") == accession
                and str(item.get("form") or "").startswith("8-K EX-99.1")
            ):
                passages.extend(
                    self.service.corpus.passages(
                        self.service.today,
                        filing["ticker"],
                        filing_id=item["id"],
                    )
                )
        return passages

    @staticmethod
    def _item_code(spec) -> str:
        for tag in getattr(spec, "tags", ()) or ():
            if str(tag).startswith("8-K-"):
                return str(tag)[4:]
        return ""

    @staticmethod
    def _item_passages(passages, item: str) -> list:
        keywords = ITEM_KEYWORDS.get(item, ())
        matched = []
        for passage in passages:
            haystack = f"{passage.section} {passage.text}".casefold()
            if item in haystack or any(keyword in haystack for keyword in keywords):
                matched.append(passage)
        return matched

    @staticmethod
    def _guidance_passages(passages) -> list:
        matched = []
        for passage in passages:
            text = " ".join(str(passage.text).split())
            if not GUIDANCE_POSITIVE.search(text):
                continue
            if GUIDANCE_NEGATIVE.search(text) or GUIDANCE_DISCLAIMER.search(text):
                continue
            # A generic "we expect growth" is not management guidance. Require
            # a concrete period or numeric target in the same passage.
            if not GUIDANCE_NUMERIC.search(text):
                continue
            matched.append(passage)
        return matched

    def _event(self, filing: dict, policy, spec, *, facts=None, sections=None, evidence_ids=None, gaps=None) -> DisclosureEvent:
        ticker = filing["ticker"]
        event_type = spec.event_type if hasattr(spec, "event_type") else spec["event_type"]
        subtype = spec.subtype if hasattr(spec, "subtype") else spec["subtype"]
        label = spec.label if hasattr(spec, "label") else spec["label"]
        materiality = spec.materiality if hasattr(spec, "materiality") else spec["materiality"]
        tags = list(getattr(spec, "tags", ())) if hasattr(spec, "tags") else list(spec.get("tags", []))
        key = _event_key(ticker, filing["id"], event_type, subtype)
        facts = facts or []
        sections = sections or []
        evidence_ids = list(dict.fromkeys(evidence_ids or []))
        gaps = list(gaps or [])
        score = {"high": 75, "medium": 50, "low": 25}.get(materiality, 25)
        if facts:
            fact_label, fact_score, hits = fact_materiality(facts)
            materiality = max_materiality(materiality, fact_label)
            score = max(score, fact_score)
        else:
            hits = []
        if sections:
            section_label = max_materiality(*(item.materiality for item in sections))
            materiality = max_materiality(materiality, section_label)
        inferences = [
            Inference(
                text="该事项可能改变分析假设，但需要结合原文和后续披露确认。",
                basis_evidence_ids=evidence_ids[:3],
            )
        ]
        return DisclosureEvent(
            id="event-" + key,
            event_key=key,
            ticker=ticker,
            filing_id=filing["id"],
            prior_filing_id=filing.get("prior_filing_id"),
            amends_filing_id=filing.get("amends_filing_id"),
            event_type=event_type,
            subtype=subtype,
            label=label or EVENT_TYPES.get(event_type, event_type),
            tags=tags,
            materiality=materiality,
            materiality_score=score,
            materiality_inputs={
                "facts": [
                    {
                        "metric": item.metric,
                        "change_pct": item.change_pct,
                        "change_abs": item.change_abs,
                        "change_pp": item.change_pp,
                    }
                    for item in facts
                ],
                "sections": [
                    {"section": item.section, "similarity": item.similarity}
                    for item in sections
                ],
            },
            materiality_rule_hits=hits,
            comparison_basis=filing.get("comparison_basis") or "first_observation",
            facts=facts,
            section_deltas=sections,
            evidence_ids=evidence_ids,
            inferences=inferences,
            gaps=gaps,
            created_at=_now(),
            updated_at=_now(),
            taxonomy_version=TAXONOMY_VERSION,
            extractor_version=EXTRACTOR_VERSION,
        )

    def build(self, filing: dict, policy, diff: dict, metadata: dict | None = None) -> list[DisclosureEvent]:
        metadata = dict(metadata or {})
        if not metadata.get("items"):
            metadata["items"] = filing.get("items") or ""
        annotated = {
            **filing,
            "prior_filing_id": diff.get("prior_filing_id"),
            "amends_filing_id": diff.get("amends_filing_id"),
            "comparison_basis": diff.get("comparison_basis") or "first_observation",
        }
        root = form_root(annotated["form"])
        if root == "8-K":
            return self._build_8k(annotated, policy, diff, metadata)
        return self._build_periodic(annotated, policy, diff)

    def _build_8k(self, filing: dict, policy, diff: dict, metadata: dict) -> list[DisclosureEvent]:
        specs = classify_8k(metadata.get("items"))
        if not specs:
            return []
        passages = self._all_passages(filing)
        events = []
        for spec in specs:
            item = self._item_code(spec)
            matched = self._item_passages(passages, item)
            gaps = []
            if not matched:
                # Preserve the evidence gap. Never let a cover page or an
                # unrelated paragraph masquerade as support for this Item.
                gaps.append(
                    f"未定位到 Item {item} 对应的正文/附件段落；证据缺失。"
                )
            event = self._event(
                filing,
                policy,
                spec,
                evidence_ids=self._evidence_ids(matched),
                gaps=gaps,
            )
            event = event.model_copy(
                update={
                    "facts": [],
                    "section_deltas": [],
                }
            )
            events.append(event)
        # Guidance is only claimed when the 8-K is an earnings release and a
        # passage contains a forward-looking statement with a period/number.
        # Explicit refusals ("we do not provide guidance") are rejected.
        if any(spec.subtype == "earnings_release" for spec in specs):
            release = [
                passage
                for passage in passages
                if passage.filing_id != filing["id"]
            ]
            # Guidance is an explicit forward-looking statement in the
            # earnings-release exhibit, not a generic Item 2.02 paragraph.
            guidance = self._guidance_passages(release)
            if guidance:
                spec = type(specs[0])(
                    "guidance",
                    "explicit_outlook",
                    "管理层指引",
                    "medium",
                    ("8-K-2.02", "guidance"),
                )
                events.append(
                    self._event(
                        filing,
                        policy,
                        spec,
                        evidence_ids=self._evidence_ids(guidance),
                    )
                )
        return events

    def _build_periodic(self, filing: dict, policy, diff: dict) -> list[DisclosureEvent]:
        facts = [FactDelta.model_validate(item) for item in diff.get("facts") or []]
        sections = [SectionDelta.model_validate(item) for item in diff.get("sections") or []]
        if not facts and not sections:
            return []
        evidence_ids: list[str] = []
        for item in facts:
            evidence_ids.extend(
                [item.current_evidence_id, item.prior_evidence_id]
            )
        for item in sections:
            evidence_ids.extend(item.evidence_ids)
        evidence_ids = [item for item in dict.fromkeys(evidence_ids) if item]
        gaps = list(diff.get("gaps") or [])
        events: list[DisclosureEvent] = []
        financial = [
            item
            for item in facts
            if item.metric
            in {
                "revenue",
                "gross_profit",
                "operating_income",
                "net_income",
                "operating_cash_flow",
                "diluted_eps",
                "gross_margin",
                "operating_margin",
                "net_margin",
            }
        ]
        liquidity = [
            item
            for item in facts
            if item.metric in {"cash", "debt", "total_assets", "operating_cash_flow", "cash_capex"}
        ]
        liquidity_sections = [
            item
            for item in sections
            if "流动" in item.section or "债务" in item.section
        ]
        legal_sections = [
            item
            for item in sections
            if "诉讼" in item.section or "法律" in item.section
        ]
        risk_sections = [item for item in sections if "风险" in item.section]
        def fact_evidence(items) -> list[str]:
            values: list[str] = []
            for item in items:
                values.extend([item.current_evidence_id, item.prior_evidence_id])
            return [value for value in dict.fromkeys(values) if value]

        def section_evidence(items) -> list[str]:
            values: list[str] = []
            for item in items:
                values.extend(item.evidence_ids)
            return [value for value in dict.fromkeys(values) if value]

        mda_sections = [item for item in sections if "管理层讨论" in item.section]
        if financial or mda_sections:
            facts_for_event = financial or facts
            materiality, score, hits = fact_materiality(facts_for_event)
            events.append(
                self._event(
                    filing,
                    policy,
                    {
                        "event_type": "financial_results",
                        "subtype": "periodic_results",
                        "label": "财务结果",
                        "materiality": materiality,
                        "tags": ["periodic", filing["form"]],
                    },
                    facts=facts_for_event,
                    sections=mda_sections,
                    evidence_ids=fact_evidence(facts_for_event) + section_evidence(mda_sections),
                    gaps=gaps + (["未取得可比较的上一期财务数字。"] if not facts else []),
                ).model_copy(
                    update={
                        "materiality_score": score,
                        "materiality_rule_hits": hits,
                    }
                )
            )
        if liquidity or liquidity_sections:
            events.append(
                self._event(
                    filing,
                    policy,
                    {
                        "event_type": "liquidity_debt",
                        "subtype": "periodic_liquidity",
                        "label": "流动性与债务",
                        "materiality": "suppressed",
                        "tags": ["periodic", filing["form"]],
                    },
                    facts=liquidity,
                    sections=liquidity_sections,
                    evidence_ids=fact_evidence(liquidity)
                    + section_evidence(liquidity_sections),
                    gaps=gaps,
                )
            )
        if legal_sections:
            events.append(
                self._event(
                    filing,
                    policy,
                    {
                        "event_type": "legal_regulatory",
                        "subtype": "legal_proceedings",
                        "label": "法律与监管",
                        "materiality": "suppressed",
                        "tags": ["periodic", filing["form"]],
                    },
                    facts=[],
                    sections=legal_sections,
                    evidence_ids=section_evidence(legal_sections),
                    gaps=gaps,
                )
            )
        if risk_sections:
            events.append(
                self._event(
                    filing,
                    policy,
                    {
                        "event_type": "legal_regulatory",
                        "subtype": "risk_factor_change",
                        "label": "风险因素变化",
                        "materiality": "suppressed",
                        "tags": ["periodic", filing["form"], "risk_factors"],
                    },
                    facts=[],
                    sections=risk_sections,
                    evidence_ids=section_evidence(risk_sections),
                    gaps=gaps,
                )
            )
        return events

    def actionable(self, events: list[DisclosureEvent], policy) -> list[DisclosureEvent]:
        floor = MATERIALITY_RANK.get(policy.materiality_floor, 2)
        allowed = event_types_for_watch(policy.watch)
        return [
            event
            for event in events
            if event.materiality != "suppressed"
            and MATERIALITY_RANK.get(event.materiality, 0) >= floor
            and (not allowed or event.event_type in allowed)
        ]
