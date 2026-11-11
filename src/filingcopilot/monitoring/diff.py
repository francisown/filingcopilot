"""Facts-first and section-targeted filing diffs.

A full-document text diff is intentionally not implemented. Annual facts and
quarterly single-quarter/YTD facts are compared on aligned periods; text is
compared only for Risk Factors, MD&A, Liquidity and Legal Proceedings.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import date
from decimal import Decimal

from ..models import PARSER_VERSION
from ..periods import period_kind
from .models import FactDelta, SectionDelta
from .taxonomy import (
    EXTRACTOR_VERSION,
    SECTION_LABELS,
    fact_materiality,
    form_root,
    is_amendment,
    section_key,
    section_materiality,
)

FACT_METRICS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "cash_capex",
    "cash",
    "debt",
    "total_assets",
    "diluted_eps",
)

MARGIN_METRICS = {"gross_margin", "operating_margin", "net_margin"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt(text: str, limit: int = 1200) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class FilingDiff:
    def __init__(self, service, store):
        self.service = service
        self.store = store

    # Snapshots -------------------------------------------------------------
    def _sections(self, filing: dict) -> dict[str, dict]:
        ticker = filing["ticker"]
        passages = self.service.corpus.passages(
            self.service.today, ticker, filing_id=filing["id"]
        )
        sections: dict[str, dict] = {}
        for passage in passages:
            # Real filings often put a sub-heading between "Item 1A. Risk
            # Factors" and the paragraph. Preserve the parent Item for grouping.
            key = (
                section_key(passage.section)
                or section_key(passage.parent_section or "")
                or "other"
            )
            entry = sections.setdefault(
                key,
                {
                    "label": SECTION_LABELS.get(
                        key, passage.parent_section or passage.section or "其他"
                    ),
                    "text": [],
                    "passages": [],
                },
            )
            entry["text"].append(passage.text)
            entry["passages"].append(passage)
        return {
            key: {
                "label": value["label"],
                "text": "\n".join(value["text"]),
                "passages": value["passages"],
            }
            for key, value in sections.items()
        }

    def snapshot(self, filing: dict) -> dict[str, dict]:
        sections = self._sections(filing)
        for key, value in sections.items():
            existing = self.store.get_snapshot(filing["id"], key)
            digest = _sha(value["text"])
            if (
                existing is None
                or existing.get("content_sha256") != digest
                or existing.get("parser_version") != PARSER_VERSION
            ):
                self.store.save_snapshot(
                    filing["id"],
                    key,
                    value["text"],
                    digest,
                    PARSER_VERSION,
                )
        return sections

    # Prior filing ----------------------------------------------------------
    def original_for_amendment(
        self, filing: dict
    ) -> tuple[dict | None, bool]:
        """Return (original, ambiguous). Same-day originals can be linked.

        If multiple base-form filings share the report period, the amendment has
        no explicit SEC parent key in the local model; do not guess.
        """
        if not is_amendment(filing["form"]):
            return None, False
        root = form_root(filing["form"])
        candidates = [
            item
            for item in self.service.corpus.catalog(
                self.service.today, filing["ticker"]
            )
            if item["id"] != filing["id"]
            and item.get("form") == root
            and item.get("period_end") == filing.get("period_end")
            and item.get("filed", "") <= filing.get("filed", "")
        ]
        candidates.sort(key=lambda item: (item["filed"], item["id"]), reverse=True)
        if len(candidates) == 1:
            return candidates[0], False
        if len(candidates) > 1:
            return None, True
        return None, False

    def prior_filing(self, filing: dict) -> tuple[dict | None, str]:
        as_of = self.service.today
        ticker = filing["ticker"]
        root = form_root(filing["form"])
        current_end = date.fromisoformat(filing["period_end"])
        candidates = [
            item
            for item in self.service.corpus.catalog(as_of, ticker)
            if item["id"] != filing["id"]
            and form_root(item["form"]) == root
            and item["period_end"] < filing["period_end"]
        ]
        if not candidates:
            return None, "first_observation"
        candidates.sort(key=lambda item: (item["period_end"], item["filed"]), reverse=True)
        if root == "10-Q":
            prior_year = [
                item
                for item in candidates
                if 350
                <= (current_end - date.fromisoformat(item["period_end"])).days
                <= 380
            ]
            if prior_year:
                prior_year.sort(key=lambda item: item["period_end"], reverse=True)
                return prior_year[0], "prior_year_quarter"
            return candidates[0], "prior_quarter"
        if root == "10-K":
            return candidates[0], "prior_annual"
        if root == "8-K":
            return candidates[0], "prior_8k"
        return candidates[0], "prior_period"

    # Facts -----------------------------------------------------------------
    INSTANT_METRICS = {"cash", "debt", "total_assets"}
    CASHFLOW_METRICS = {"operating_cash_flow", "cash_capex"}
    INCOME_METRICS = {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "diluted_eps",
    }

    def _fact_rows(
        self,
        ticker: str,
        metric: str,
        fiscal_year: int,
        forms: tuple[str, str],
        before_filed: str | None = None,
    ) -> list[tuple]:
        rows = []
        for fact in self.service.corpus.facts(
            ticker, metric, fiscal_year, self.service.today, forms=forms
        ):
            evidence = self.service.corpus.evidence(fact.id, self.service.today)
            if not evidence:
                continue
            filed = str((evidence.get("filing") or {}).get("filed") or "")
            if before_filed and filed > before_filed:
                continue
            rows.append((fact, evidence, filed))
        return rows

    def _select_current_fact(
        self, rows: list[tuple], metric: str, filing_id: str, period_end: str, root: str
    ) -> tuple:
        current = [row for row in rows if row[0].filing_id == filing_id]
        if not current:
            return None, None
        if metric in self.INSTANT_METRICS:
            instant = [
                row
                for row in current
                if row[0].start is None and row[0].end.isoformat() == period_end
            ]
            return (instant[0], "instant") if instant else (None, None)
        duration = [row for row in current if row[0].start is not None]
        if root == "10-K":
            annual = [
                row
                for row in duration
                if period_kind(row[0].start, row[0].end, "10-K") == "annual"
            ]
            return (annual[0], "annual") if annual else (None, None)
        if metric in self.CASHFLOW_METRICS:
            ytd = [
                row
                for row in duration
                if period_kind(row[0].start, row[0].end, "10-Q") in ("ytd6", "ytd9")
            ]
            if ytd:
                return ytd[0], period_kind(ytd[0][0].start, ytd[0][0].end, "10-Q")
        quarter = [
            row
            for row in duration
            if period_kind(row[0].start, row[0].end, "10-Q") == "quarter"
        ]
        if quarter:
            return quarter[0], "quarter"
        ytd = [
            row
            for row in duration
            if period_kind(row[0].start, row[0].end, "10-Q") in ("ytd6", "ytd9")
        ]
        if ytd:
            return ytd[0], period_kind(ytd[0][0].start, ytd[0][0].end, "10-Q")
        return None, None

    @staticmethod
    def _comparable(current_fact, candidate, kind: str) -> bool:
        days = (current_fact.end - candidate.end).days
        if kind == "instant":
            return 350 <= days <= 380
        if kind == "annual":
            return 300 <= days <= 400
        if kind in ("quarter", "ytd6", "ytd9"):
            if period_kind(candidate.start, candidate.end, "10-Q") != kind:
                return False
            return 350 <= days <= 380
        return False

    def _prior_fact(
        self,
        filing: dict,
        metric: str,
        current_fact,
        kind: str,
        fiscal_year: int,
        forms: tuple[str, str],
        root: str,
    ) -> tuple:
        rows = self._fact_rows(
            filing["ticker"], metric, fiscal_year, forms,
            before_filed=filing["filed"],
        )
        same_filing = [
            row
            for row in rows
            if row[0].filing_id == filing["id"]
            and self._comparable(current_fact, row[0], kind)
        ]
        if same_filing:
            return same_filing[0]
        other = [
            row
            for row in rows
            if row[0].filing_id != filing["id"]
            and self._comparable(current_fact, row[0], kind)
        ]
        other.sort(key=lambda row: (row[2], row[0].end), reverse=True)
        return other[0] if other else (None, None, None)

    def facts_delta(self, filing: dict, prior: dict | None, basis: str) -> tuple[list[FactDelta], list[str]]:
        gaps: list[str] = []
        deltas: list[FactDelta] = []
        periods: dict[str, dict] = {}
        ticker = filing["ticker"]
        root = form_root(filing["form"])
        if root not in {"10-K", "10-Q"}:
            return [], gaps
        year = int(filing["period_end"][:4])
        forms = ("10-K", "10-K/A") if root == "10-K" else ("10-Q", "10-Q/A")
        for metric in FACT_METRICS:
            current_rows = self._fact_rows(ticker, metric, year, forms)
            current_row, kind = self._select_current_fact(
                current_rows, metric, filing["id"], filing["period_end"], root
            )
            current_fact = current_row[0] if current_row else None
            if current_fact is None:
                if metric in self.INSTANT_METRICS:
                    gaps.append(f"{metric} 缺少当前 filing 的期末余额。")
                continue
            prior_fact, _prior_evidence, _prior_filed = self._prior_fact(
                filing,
                metric,
                current_fact,
                kind,
                year - 1,
                forms,
                root,
            )
            if prior_fact is None:
                gaps.append(f"{metric} 缺少上年同期同口径数字。")
                continue
            if current_fact.unit != prior_fact.unit:
                gaps.append(f"{metric} 当前与上期单位不一致，未计算。")
                continue
            current_value = Decimal(current_fact.value)
            prior_value = Decimal(prior_fact.value)
            change_abs = current_value - prior_value
            change_pct = (
                float(change_abs / abs(prior_value) * 100)
                if prior_value != 0
                else None
            )
            comparison = (
                "prior_annual"
                if root == "10-K"
                else "prior_year_instant"
                if kind == "instant"
                else "prior_year_ytd"
                if kind in ("ytd6", "ytd9")
                else "prior_year_quarter"
            )
            delta = FactDelta(
                metric=metric,
                label=current_fact.label,
                current_value=str(current_value),
                prior_value=str(prior_value),
                unit=current_fact.unit,
                change_pct=round(change_pct, 4) if change_pct is not None else None,
                change_abs=str(change_abs),
                comparison_basis=comparison,
                current_evidence_id=current_fact.id,
                prior_evidence_id=prior_fact.id,
            )
            label, _score, _hits = fact_materiality([delta])
            deltas.append(delta.model_copy(update={"materiality": label}))
            periods[metric] = {
                "kind": kind,
                "current_start": current_fact.start,
                "current_end": current_fact.end,
                "prior_start": prior_fact.start,
                "prior_end": prior_fact.end,
                "unit": current_fact.unit,
            }

        # Margin changes are calculated from the already-bound current/prior
        # values; they never fetch a different filing version.
        by_metric = {delta.metric: delta for delta in deltas}
        revenue = by_metric.get("revenue")
        if revenue and Decimal(revenue.current_value) != 0 and revenue.prior_value:
            prior_revenue = Decimal(revenue.prior_value)
            if prior_revenue != 0:
                for metric, numerator, label in (
                    ("gross_margin", "gross_profit", "毛利率"),
                    ("operating_margin", "operating_income", "营业利润率"),
                    ("net_margin", "net_income", "净利率"),
                ):
                    part = by_metric.get(numerator)
                    if not part or part.prior_value is None:
                        continue
                    revenue_period = periods.get("revenue")
                    part_period = periods.get(numerator)
                    if (
                        not revenue_period
                        or not part_period
                        or revenue_period["kind"] != part_period["kind"]
                        or revenue_period["current_start"] != part_period["current_start"]
                        or revenue_period["current_end"] != part_period["current_end"]
                        or revenue_period["prior_start"] != part_period["prior_start"]
                        or revenue_period["prior_end"] != part_period["prior_end"]
                        or revenue_period["unit"] != part_period["unit"]
                    ):
                        gaps.append(
                            f"{metric} 分子与收入的期间/口径/单位不一致，未计算。"
                        )
                        continue
                    current_margin = (
                        Decimal(part.current_value) / Decimal(revenue.current_value) * 100
                    )
                    prior_margin = Decimal(part.prior_value) / prior_revenue * 100
                    change_pp = current_margin - prior_margin
                    delta = FactDelta(
                        metric=metric,
                        label=label,
                        current_value=f"{current_margin:.2f}",
                        prior_value=f"{prior_margin:.2f}",
                        unit="%",
                        change_pct=None,
                        change_abs=f"{change_pp:.2f}",
                        change_pp=round(float(change_pp), 4),
                        comparison_basis=revenue.comparison_basis,
                        current_evidence_id=part.current_evidence_id,
                        prior_evidence_id=part.prior_evidence_id,
                    )
                    margin_label, _score, _hits = fact_materiality([delta])
                    deltas.append(
                        delta.model_copy(update={"materiality": margin_label})
                    )
        return deltas, gaps

    # Sections --------------------------------------------------------------
    def section_deltas(self, filing: dict, prior: dict | None, current_sections: dict, prior_sections: dict) -> list[SectionDelta]:
        deltas: list[SectionDelta] = []
        for key in SECTION_LABELS:
            current = current_sections.get(key)
            previous = prior_sections.get(key) if prior_sections else None
            current_text = current["text"] if current else ""
            prior_text = previous["text"] if previous else ""
            if not current_text or not prior_text:
                if current_text and not prior_text:
                    deltas.append(
                        SectionDelta(
                            section=current["label"],
                            similarity=0.0,
                            added_chars=len(current_text),
                            excerpt=_excerpt(current_text, 600),
                            char_start=0,
                            char_end=len(current_text),
                            char_text=current_text,
                            section_text=current_text,
                            quote_hash=_sha(_excerpt(current_text, 600)),
                            semantic_summary=_excerpt(current_text, 600),
                            summary_kind="excerpt",
                            evidence_ids=[item.id for item in current["passages"][:5]],
                            materiality="medium",
                        )
                    )
                continue
            matcher = difflib.SequenceMatcher(None, prior_text, current_text)
            similarity = round(matcher.ratio(), 4)
            if similarity >= 0.995:
                continue
            added = []
            added_chars = 0
            removed_chars = 0
            change_start = None
            change_end = None
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag in {"insert", "replace"}:
                    added_chars += max(0, j2 - j1)
                    added.append(current_text[j1:j2])
                    if change_start is None:
                        change_start, change_end = j1, j2
                if tag in {"delete", "replace"}:
                    removed_chars += max(0, i2 - i1)
            excerpt = _excerpt("\n".join(added) if added else current_text, 900)
            evidence_ids = [
                item.id
                for item in current["passages"]
                if excerpt[:80] and excerpt[:80] in item.text
            ][:5]
            if not evidence_ids:
                evidence_ids = [item.id for item in current["passages"][:3]]
            deltas.append(
                SectionDelta(
                    section=current["label"],
                    similarity=similarity,
                    added_chars=added_chars,
                    removed_chars=removed_chars,
                    excerpt=excerpt,
                    char_start=change_start,
                    char_end=change_end,
                    char_text=(
                        current_text[change_start:change_end]
                        if change_start is not None and change_end is not None
                        else ""
                    ),
                    section_text=current_text,
                    quote_hash=_sha(excerpt),
                    semantic_summary=excerpt,
                    summary_kind="excerpt",
                    evidence_ids=evidence_ids,
                    materiality=section_materiality(similarity, key),
                )
            )
        return deltas

    # Build -----------------------------------------------------------------
    def build(self, filing: dict, prior: dict | None = None) -> dict:
        amendment, ambiguous_amendment = self.original_for_amendment(filing)
        if amendment is not None:
            prior, basis = amendment, "amendment"
        else:
            prior, basis = (
                (prior, "prior_period")
                if prior is not None
                else self.prior_filing(filing)
            )
        current_sections = self.snapshot(filing)
        prior_sections = self.snapshot(prior) if prior is not None else {}
        facts, gaps = self.facts_delta(filing, prior, basis)
        if ambiguous_amendment:
            gaps.append(
                "修订公告存在多个同期间原件，无法唯一关联；未自动 supersede。"
            )
        sections = self.section_deltas(filing, prior, current_sections, prior_sections)
        payload = {
            "id": "diff-" + hashlib.sha256(
                f"{filing['id']}|{basis}|{EXTRACTOR_VERSION}".encode()
            ).hexdigest()[:20],
            "ticker": filing["ticker"],
            "filing_id": filing["id"],
            "prior_filing_id": prior["id"] if prior else None,
            "amends_filing_id": amendment["id"] if amendment else None,
            "comparison_basis": basis,
            "extractor_version": EXTRACTOR_VERSION,
            "facts": [item.model_dump() for item in facts],
            "sections": [item.model_dump() for item in sections],
            "gaps": gaps,
            "as_of": self.service.today.isoformat(),
        }
        self.store.save_diff(payload)
        return payload
