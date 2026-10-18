"""Offline package consistency checks, with optional trusted local source comparison.

Checksums detect changes to an existing package, not its author or authenticity.
No model, network access or expression evaluation is used by this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import Field

from . import __version__
from .domain import Domain
from .market import MarketEvidence, MarketRouter, parse_market
from .models import (
    Calculation,
    Claim,
    ExternalUpdate,
    Fact,
    Filing,
    Passage,
    ResearchAnswer,
    StatementFact,
    StrictModel,
    Submission,
)
from .periods import period_kind
from .tools import ResearchTools
from .valuation import ValuationEvidence, parse_eps
from .verification import render_numeric, verify_submission

SCHEMA = "filingcopilot.research-package/v1"


def digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Package(StrictModel):
    schema_version: Literal["filingcopilot.research-package/v1"]
    producer_version: str
    domain_origin: Literal["research_time", "export_time_legacy"]
    focus_ticker: str | None
    domain: Domain
    research: ResearchAnswer
    manifest: dict[str, str]


class SavedClaim(StrictModel):
    index: int = Field(strict=True, ge=0)
    kind: Literal["fact", "calculation", "market", "valuation", "narrative", "excerpt", "analysis"]
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    rendering: Literal["annual_detail_v1"] | None = None
    verification: Literal[
        "deterministic", "semantic_review", "exact_source_excerpt", "inference_review"
    ]


def create_package(
    answer: ResearchAnswer,
    domain: Domain,
    focus_ticker: str | None = None,
    *,
    legacy: bool = False,
) -> dict:
    body = {
        "schema_version": SCHEMA,
        "producer_version": __version__,
        "domain_origin": "export_time_legacy" if legacy else "research_time",
        "focus_ticker": focus_ticker,
        "domain": domain.model_dump(mode="json"),
        "research": answer.model_dump(mode="json"),
    }
    return {**body, "manifest": {"algorithm": "sha256", "payload_sha256": digest(body)}}


class Snapshot:
    def __init__(self, evidence: dict[str, dict]):
        self.items = evidence

    def evidence(self, evidence_id: str, as_of: date) -> dict | None:
        item = self.items.get(evidence_id)
        if item and item["kind"] == "external_update":
            update = ExternalUpdate.model_validate({k: v for k, v in item.items() if k != "kind"})
            return item if update.available_day <= as_of else None
        if (
            item
            and item["kind"] not in ("calculation", "market", "valuation")
            and date.fromisoformat(item["filing"]["filed"]) <= as_of
        ):
            return item
        return None


def collect_evidence(items: list[dict]) -> dict[str, dict]:
    result = {}
    for item in items:
        kind = item.get("kind")
        if kind == "calculation":
            Calculation.model_validate(
                {k: v for k, v in item.items() if k not in ("kind", "inputs")}
            )
            inputs = item.get("inputs", [])
            required = ["market", "fact"] if item["operation"] == "annual_pe" else ["fact", "fact"]
            if item["operation"] == "provider_ttm_pe":
                required = ["market", "valuation"]
            if item["operation"] == "trailing_sum":
                if len(inputs) not in (1, 3):
                    raise ValueError("TTM requires one full-year or three aligned flow inputs")
                required = [inputs[0].get("kind")] * len(inputs)
                if required[0] not in ("fact", "calculation"):
                    raise ValueError("TTM input must be a flow fact or cash-flow calculation")
            elif item["operation"] == "free_cash_flow" and len(inputs) == 3:
                required = ["fact"] * 3
            elif item["operation"] in ("growth", "difference", "ratio", "net_cash_capex") and all(
                e.get("kind") == "calculation" for e in inputs
            ):
                required = ["calculation"] * 2
            if [e.get("kind") for e in inputs] != required:
                raise ValueError("Calculation input types differ from the supported formula")
            children = collect_evidence(inputs)
        elif kind == "market":
            MarketEvidence.model_validate(item)
            children = {}
        elif kind == "valuation":
            ValuationEvidence.model_validate(item)
            children = {}
        elif kind in ("fact", "statement_fact", "passage"):
            model = Fact if kind == "fact" else StatementFact if kind == "statement_fact" else Passage
            model.model_validate({k: v for k, v in item.items() if k not in ("kind", "filing")})
            Filing.model_validate(item["filing"])
            children = {}
        elif kind == "external_update":
            ExternalUpdate.model_validate({k: v for k, v in item.items() if k != "kind"})
            children = {}
        else:
            raise ValueError("Unknown evidence type")
        for key, value in {**children, item["id"]: item}.items():
            if key in result and digest(result[key]) != digest(value):
                raise ValueError(f"Conflicting snapshots for evidence {key}")
            result[key] = value
    return result


def audit_package(
    payload: dict,
    *,
    corpus=None,
    trusted_domain: Domain | None = None,
    source_cache: Path | None = None,
    market_cache: Path | None = None,
    external_cache: Path | None = None,
) -> dict:
    if isinstance(payload, dict) and payload.get("schema_version") == "filingcopilot.research-attribution/v1":
        from .attribution import audit_attribution

        return audit_attribution(
            payload, corpus=corpus, trusted_domain=trusted_domain,
            source_cache=source_cache, market_cache=market_cache, external_cache=external_cache,
        )
    checks = []

    def check(code, condition, detail, evidence_id=None):
        checks.append(
            {
                "code": code,
                "status": "pass" if condition else "fail",
                "detail": detail,
                **({"evidence_id": evidence_id} if evidence_id else {}),
            }
        )

    source_matches, source_total, numeric_count, narrative_count = 0, 0, 0, 0
    raw = {"filings_checked": 0, "facts_checked": 0, "missing": 0}
    market_sources = {"requested": market_cache is not None, "checked": 0, "missing": 0}
    valuation_sources = {"requested": market_cache is not None, "checked": 0, "missing": 0}
    try:
        package = Package.model_validate(payload)
        body = {key: value for key, value in payload.items() if key != "manifest"}
        check(
            "manifest",
            package.manifest == {"algorithm": "sha256", "payload_sha256": digest(body)},
            "包内容的 SHA256 一致；此校验和不证明作者或来源真实性。",
        )
        if trusted_domain is not None:
            configuration_matches = (
                all(
                    all(
                        k in getattr(trusted_domain, key) and v == getattr(trusted_domain, key)[k]
                        for k, v in getattr(package.domain, key).items()
                    )
                    for key in (
                        "metrics",
                        "ratios",
                        "query_expansions",
                        "currencies",
                    )
                )
                and all(
                    ticker in trusted_domain.financial_profiles
                    and profile.cik == trusted_domain.financial_profiles[ticker].cik
                    and profile.currency == trusted_domain.financial_profiles[ticker].currency
                    and profile.taxonomy == trusted_domain.financial_profiles[ticker].taxonomy
                    and all(
                        key in trusted_domain.financial_profiles[ticker].metrics
                        and metric == trusted_domain.financial_profiles[ticker].metrics[key]
                        for key, metric in profile.metrics.items()
                    )
                    and all(
                        key in trusted_domain.financial_profiles[ticker].ratio_labels
                        and label == trusted_domain.financial_profiles[ticker].ratio_labels[key]
                        for key, label in profile.ratio_labels.items()
                    )
                    for ticker, profile in package.domain.financial_profiles.items()
                )
                and all(
                    key in trusted_domain.companies
                    and company.cik == trusted_domain.companies[key].cik
                    and company.name == trusted_domain.companies[key].name
                    and set(company.aliases) <= set(trusted_domain.companies[key].aliases)
                    for key, company in package.domain.companies.items()
                )
            )
            check(
                "domain",
                configuration_matches,
                "包中的指标、比率和币种配置须与指定的本地配置一致。",
            )
        research = package.research
        check(
            "answer_status",
            (research.status == "answered" and bool(research.answer) and not research.gaps)
            or (research.status == "partial" and bool(research.answer) and bool(research.gaps))
            or (research.status in ("abstained", "error") and not research.answer),
            "完成、部分回答和拒答状态须与实际结论及缺口一致。",
        )
        if package.domain_origin == "export_time_legacy":
            checks.append(
                {
                    "code": "legacy_configuration",
                    "status": "not_checked",
                    "detail": "旧研究记录没有冻结当时配置；此包使用导出时配置。",
                }
            )
        ledger = collect_evidence(research.evidence)
        contrarian = research.contrarian or {}
        if contrarian.get("status") not in ("found", "not_found", "not_run"):
            check("contrarian_structure", False, "反证分区状态无效。")
        elif contrarian.get("status") == "found":
            items = contrarian.get("items", [])
            valid = bool(items) and all(
                item.get("title") == "反证"
                and item.get("verification") == "exact_source_excerpt"
                and len(item.get("evidence_ids", [])) == 1
                and item["evidence_ids"][0] in ledger
                and ledger[item["evidence_ids"][0]].get("kind") == "passage"
                and item.get("text") == ledger[item["evidence_ids"][0]].get("text")
                for item in items
            )
            check(
                "contrarian_evidence",
                valid,
                "反证条目须是研究包内同一条原文摘录，并绑定唯一证据 ID。",
            )
        elif contrarian.get("status") == "not_found":
            check(
                "contrarian_empty_disclosed",
                "未检索到反证" in str(contrarian.get("note", "")),
                "无反证时必须明确披露本轮未检索到反证。",
            )
        tools = ResearchTools(
            Snapshot(ledger), SimpleNamespace(domain=package.domain), research.as_of
        )
        tools.ledger = dict(ledger)
        if contrarian.get("status") == "found":
            for item in contrarian.get("items", []):
                verified, errors = verify_submission(
                    Submission(
                        claims=[
                            Claim(
                                kind="excerpt",
                                evidence_ids=item.get("evidence_ids", []),
                                statement=item.get("text", ""),
                            )
                        ]
                    ),
                    tools,
                    "反证检索：" + research.question,
                    focus_ticker=package.focus_ticker,
                    allow_excerpts=True,
                )
                check(
                    "contrarian_source",
                    len(verified) == 1 and not errors,
                    "反证摘录须通过与普通逐字摘录相同的公开日期、公司归属和原文核验。",
                    item.get("evidence_ids", [None])[0],
                )
        filings = {}
        for key, item in ledger.items():
            if item["kind"] == "external_update":
                from .cninfo import CNInfoSource, parse_cninfo_announcement
                from .external_updates import ExternalSource, parse_feed, parse_transcript
                from .hkex import HKEXSource, parse_hkex_announcement

                update = ExternalUpdate.model_validate({k: v for k, v in item.items() if k != "kind"})
                if update.source_type == "hkex":
                    source = HKEXSource.model_validate(
                        {
                            k: v
                            for k, v in update.source_config.items()
                            if k in ("source_type", "source_name", "stock_code", "market", "search_host")
                        }
                    )
                    source_feed_matches = update.source_feed_url == source.search_url
                elif update.source_type == "cninfo":
                    source = CNInfoSource.model_validate(
                        {
                            k: v
                            for k, v in update.source_config.items()
                            if k in ("source_type", "source_name", "stock_code", "column", "search_host", "static_host")
                        }
                    )
                    source_feed_matches = update.source_feed_url == source.query_url
                else:
                    source = ExternalSource.model_validate(update.source_config)
                    source_feed_matches = source.feed_url == update.source_feed_url
                expected_level = (
                    "hkex_official"
                    if update.source_type == "hkex"
                    else "cninfo_official"
                    if update.source_type == "cninfo"
                    else source.evidence_level
                )
                check("external_scope", update.available_day <= research.as_of
                      and source.source_type == update.source_type
                      and source.source_name == update.source_name
                      and expected_level == update.evidence_level
                      and source_feed_matches,
                      "外部来源类型、等级、公开/首次取得时间须与研究范围一致；不视作 SEC 申报。", key)
                if corpus is not None:
                    matches = corpus.evidence(key, research.as_of) == item
                    source_total += 1
                    source_matches += matches
                    check("external_source", matches, "与独立指定的外部来源存储比较。", key)
                if external_cache is not None:
                    path = external_cache / "raw" / (update.content_sha256 + ".cache")
                    if path.is_file():
                        content = path.read_bytes()
                        if update.source_type == "hkex":
                            extracted = parse_hkex_announcement(
                                content,
                                source,
                                update.ticker,
                                {
                                    "language": update.language,
                                    "source_url": update.source_url,
                                    "title": update.title,
                                    "published_at": update.published_at,
                                    "search_url": update.source_feed_url,
                                    "document_title": update.source_config.get("document_title", ""),
                                    "counterpart_url": update.source_config.get("counterpart_url"),
                                    "index_sha256": update.source_config.get("index_sha256"),
                                },
                                update.retrieved_at,
                            )
                        elif update.source_type == "cninfo":
                            extracted = parse_cninfo_announcement(
                                content,
                                source,
                                update.ticker,
                                {
                                    "announcement_id": update.source_config.get("announcement_id", ""),
                                    "adjunct_url": update.source_config.get("adjunct_url", ""),
                                    "source_url": update.source_url,
                                    "title": update.title,
                                    "published_at": update.published_at,
                                    "index_sha256": update.source_config.get("index_sha256"),
                                },
                                update.retrieved_at,
                            )
                        else:
                            parsed = (
                                parse_feed(content, source, update.ticker, update.retrieved_at)
                                if source.format == "rss"
                                else parse_transcript(
                                    content,
                                    source,
                                    update.ticker,
                                    update.source_url,
                                    update.retrieved_at,
                                )
                            )
                            extracted = next((u for u in parsed if u.id == update.id), None)
                        expected = extracted
                        matches = hashlib.sha256(content).hexdigest() == update.content_sha256 and expected == update
                        source_total += 1
                        source_matches += matches
                        check("raw_external_update", matches, "从冻结外部原件重提取摘要、来源链接及公开时间；不验证报道事实全部真实。", key)
                    else:
                        checks.append({"code": "raw_external_missing", "status": "not_checked", "detail": "外部来源原件缓存缺失。", "evidence_id": key})
                continue
            if item["kind"] == "valuation":
                snapshot = ValuationEvidence.model_validate(item).snapshot
                check(
                    "valuation_scope",
                    snapshot.retrieved_at.date() <= research.as_of,
                    "供应商 EPS 获取日期不得晚于知悉日期；期间标签不视为 SEC 财季日期。",
                    key,
                )
                if market_cache is not None:
                    path = market_cache / "raw" / (snapshot.source_sha256 + ".json")
                    if path.is_file():
                        valuation_sources["checked"] += 1
                        content = path.read_bytes()
                        expected = parse_eps(
                            content,
                            snapshot.ticker,
                            snapshot.retrieved_at,
                            snapshot.source_url,
                            stale=snapshot.stale,
                        )
                        matches = (
                            expected == snapshot
                            and hashlib.sha256(content).hexdigest() == snapshot.source_sha256
                        )
                        source_total += 1
                        source_matches += matches
                        check(
                            "raw_valuation",
                            matches,
                            "从供应商原始响应重提取 TTM EPS、币种和期末标签；不证明其 EPS 方法正确。",
                            key,
                        )
                    else:
                        valuation_sources["missing"] += 1
                        checks.append(
                            {
                                "code": "raw_valuation_missing",
                                "status": "not_checked",
                                "detail": "供应商 EPS 原始缓存缺失。",
                                "evidence_id": key,
                            }
                        )
                continue
            if item["kind"] == "market":
                snapshot = MarketEvidence.model_validate(item).snapshot
                check(
                    "market_scope",
                    snapshot.as_of <= research.as_of,
                    "行情交易日和报价不得晚于知悉日期。",
                    key,
                )
                reference = snapshot.cross_check_source
                if reference is not None:
                    check(
                        "market_cross_check_scope",
                        reference.as_of <= research.as_of,
                        "交叉核对的来源也不得晚于研究知悉日期。",
                        key,
                    )
                if market_cache is not None:
                    path = market_cache / "raw" / (snapshot.source_sha256 + ".json")
                    if path.is_file():
                        market_sources["checked"] += 1
                        content = path.read_bytes()
                        expected = parse_market(
                            content,
                            snapshot.ticker,
                            snapshot.period,
                            snapshot.as_of,
                            snapshot.retrieved_at,
                            snapshot.source_url,
                            stale=snapshot.stale,
                        )
                        # Source warnings/conflicts are derived by the router, not
                        # supplied by the primary raw response. Check the primary
                        # fields first, then replay the router from both originals.
                        derived = (
                            bool(snapshot.source_conflicts)
                            or snapshot.warnings != expected.warnings
                        )
                        base = snapshot.model_copy(
                            update={
                                "cross_check_source": None,
                                "source_conflicts": [],
                                "warnings": expected.warnings,
                            }
                        )
                        matches = (
                            snapshot.source_sha256 == hashlib.sha256(content).hexdigest()
                            and expected == base
                            and snapshot.warnings[: len(expected.warnings)] == expected.warnings
                        )
                        source_total += 1
                        source_matches += matches
                        check(
                            "raw_market",
                            matches,
                            "从独立指定的行情原始响应重新提取价格、日期、币种及报价。",
                            key,
                        )
                        secondary_path = (
                            market_cache / "raw" / (reference.source_sha256 + ".json")
                            if reference is not None
                            else None
                        )
                        if secondary_path is not None and secondary_path.is_file():
                            market_sources["checked"] += 1
                            secondary_raw = secondary_path.read_bytes()
                            secondary = parse_market(
                                secondary_raw,
                                reference.ticker,
                                reference.period,
                                reference.as_of,
                                reference.retrieved_at,
                                reference.source_url,
                                stale=reference.stale,
                            )
                            replay = MarketRouter._with_conflict(expected, secondary)
                            comparison_matches = (
                                reference.source_sha256 == hashlib.sha256(secondary_raw).hexdigest()
                                and replay == snapshot
                            )
                            source_total += 1
                            source_matches += comparison_matches
                            check(
                                "raw_market_cross_check",
                                comparison_matches,
                                "从两家供应商原始响应重放同日收盘值、差异百分比和来源提示；不证明供应商报价本身正确。",
                                key,
                            )
                        elif reference is not None or derived:
                            market_sources["missing"] += 1
                            checks.append(
                                {
                                    "code": "raw_market_cross_check_missing",
                                    "status": "not_checked",
                                    "detail": "交叉核对来源的原始缓存或旧包来源引用缺失；仅核对主来源，未确认两家差异及附加提示。",
                                    "evidence_id": key,
                                }
                            )
                    else:
                        market_sources["missing"] += 1
                        checks.append(
                            {
                                "code": "raw_market_missing",
                                "status": "not_checked",
                                "detail": "行情原始响应缺失，无法独立确认行情来源。",
                                "evidence_id": key,
                            }
                        )
                continue
            if item["kind"] == "calculation":
                continue
            filing = Filing.model_validate(item["filing"])
            if filing.id in filings and filings[filing.id] != filing:
                raise ValueError("One filing ID has conflicting snapshots")
            filings[filing.id] = filing
            check(
                "evidence_scope",
                item["filing_id"] == filing.id
                and item["ticker"] == filing.ticker
                and filing.filed <= research.as_of
                and filing.period_end <= filing.filed,
                "主体、申报归属和公开日期须与研究知悉日期相容。",
                key,
            )
            if item["kind"] == "fact":
                fact = Fact.model_validate(
                    {k: v for k, v in item.items() if k not in ("kind", "filing")}
                )
                metric = (
                    package.domain.metric_for(fact.ticker, fact.metric)
                    if fact.metric in package.domain.metrics
                    else None
                )
                profile = package.domain.financial_profiles.get(fact.ticker)
                annual = filing.form in ("10-K", "10-K/A", "20-F", "20-F/A")
                check(
                    "annual_fact" if annual else "quarterly_fact",
                    metric is not None
                    and fact.unit == metric.unit
                    and fact.label == metric.label
                    and fact.end.year == fact.fiscal_year
                    and fact.end <= filing.period_end
                    and period_kind(fact.start, fact.end, filing.form) is not None
                    and (
                        not filing.form.startswith("20-F")
                        or (
                            profile is not None
                            and profile.cik == filing.cik
                            and fact.metric in profile.metrics
                        )
                    )
                    and (
                        annual
                        or fact.end == filing.period_end
                        or 357 <= (filing.period_end - fact.end).days <= 373
                    ),
                    "事实的指标、币种、标签及申报期间须有效；年度、单季度与累计期间分开检查。",
                    key,
                )
            elif item["kind"] == "statement_fact":
                row = StatementFact.model_validate(
                    {k: v for k, v in item.items() if k not in ("kind", "filing")}
                )
                check(
                    "statement_fact",
                    row.statement in ("income", "balance", "cashflow", "other")
                    and row.end.year == row.fiscal_year
                    and row.end <= filing.period_end
                    and (row.start is None or period_kind(row.start, row.end, filing.form) is not None
                         or 357 <= (filing.period_end - row.end).days <= 373),
                    "三表行须保留原始概念、单位、期间和维度；缺失不按零处理。",
                    key,
                )
            if corpus is not None:
                original = corpus.evidence(key, research.as_of)
                matches = original is not None and digest(original) == digest(item)
                source_total += 1
                source_matches += matches
                check("corpus_source", matches, "与独立指定的本地证据库逐字段比较。", key)

        for key, item in ledger.items():
            if item["kind"] != "calculation":
                continue
            try:
                if item["input_ids"] != [e["id"] for e in item["inputs"]]:
                    raise ValueError("Input IDs differ from input snapshots")
                expected = tools.calculate(item["operation"], *item["input_ids"])["evidence"]
                matches = Decimal(item["value"]) == Decimal(expected["value"]) and all(
                    item[field] == expected[field]
                    for field in (
                        "id",
                        "operation",
                        "input_ids",
                        "unit",
                        "label",
                        "formula",
                        "ticker",
                        "fiscal_year",
                    )
                )
                check(
                    "calculation",
                    matches,
                    "从绑定的事实重新执行 Decimal 计算并核对公式、单位和期间。",
                    key,
                )
            except (ValueError, ArithmeticError) as exc:
                check("calculation", False, str(exc), key)

        trend_steps = [t for t in research.trace if t.get("stage") == "financial_trend"]
        if research.mode == "trend" or trend_steps:
            from .trends import validate_trend

            try:
                if len(trend_steps) != 1 or research.mode != "trend":
                    raise ValueError("Financial trend requires exactly one frozen trend step")
                validate_trend(trend_steps[0]["result"], ledger, package.domain, research.as_of)
                check(
                    "financial_trend",
                    True,
                    "核对各年指标、完整年度期间、同一申报版本和自由现金流毛额输入；缺失年份不补零。",
                )
            except (ValueError, KeyError, TypeError) as exc:
                check("financial_trend", False, str(exc))

        comparison_steps = [t for t in research.trace if t.get("stage") == "company_comparison"]
        if research.mode == "comparison" or comparison_steps:
            from .comparison import validate_comparison

            try:
                if len(comparison_steps) != 1 or research.mode != "comparison":
                    raise ValueError("Comparison requires exactly one frozen comparison step")
                validate_comparison(
                    comparison_steps[0]["result"], ledger, package.domain, research.as_of
                )
                check(
                    "company_comparison",
                    True,
                    "从绑定日线重算共同日期、统一价格口径与起点 100；核对表格指标、公司和财年。",
                )
            except (ValueError, KeyError, TypeError) as exc:
                check("company_comparison", False, str(exc))

        historical_steps = [t for t in research.trace if t.get("stage") == "historical_valuation"]
        if historical_steps:
            from .historical_valuation import validate_historical_reference

            try:
                if len(historical_steps) != 1 or research.mode != "market":
                    raise ValueError("Historical valuation requires one frozen market step")
                validate_historical_reference(historical_steps[0]["result"], ledger, research.as_of)
                check(
                    "historical_valuation",
                    True,
                    "从冻结日线、公开日期前年度 EPS、币种及拆股边界重算历史年度市盈率；缺口保持为空。",
                )
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                check("historical_valuation", False, str(exc))

        peer_steps = [t for t in research.trace if t.get("stage") == "peer_selection"]
        if peer_steps:
            from .peers import validate_peer_selection

            try:
                if len(peer_steps) != 1 or len(comparison_steps) != 1:
                    raise ValueError("Peer selection needs one frozen comparison")
                validate_peer_selection(peer_steps[0]["result"], comparison_steps[0]["result"])
                check(
                    "peer_selection",
                    True,
                    "核对冻结候选组、所选证券与对比范围；候选组不是正式行业分类。",
                )
            except (ValueError, KeyError, TypeError) as exc:
                check("peer_selection", False, str(exc))

        scenario_steps = [t for t in research.trace if t.get("stage") == "valuation_scenarios"]
        if research.mode == "scenarios" or scenario_steps:
            from .scenarios import validate_scenarios

            try:
                if research.mode != "scenarios" or len(scenario_steps) != 1:
                    raise ValueError("Scenario research needs one frozen calculation step")
                validate_scenarios(
                    scenario_steps[0]["result"], ledger, research.as_of, package.domain
                )
                check(
                    "valuation_scenarios",
                    True,
                    "从引用的 EPS、报价与显式假设重算情景；不核验假设是否会实现。",
                )
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                check("valuation_scenarios", False, str(exc))

        report_steps = [t for t in research.trace if t.get("stage") == "company_report_layout"]
        if research.mode == "company_report" or report_steps:
            from .company_report import validate_report_layout

            try:
                if research.mode != "company_report" or len(report_steps) != 1:
                    raise ValueError("Company report needs one frozen section layout")
                validate_report_layout(report_steps[0]["sections"], research.answer)
                check(
                    "company_report_layout",
                    True,
                    "核对六节布局、引用索引与分析/摘录的核验类别；空节不表示已完成研究。",
                )
            except (ValueError, KeyError, TypeError) as exc:
                check("company_report_layout", False, str(exc))

        claims = [SavedClaim.model_validate(c) for c in research.answer]
        if len({c.index for c in claims}) != len(claims):
            raise ValueError("Duplicate claim indexes")
        submission = Submission(
            claims=[
                Claim(
                    kind=c.kind,
                    evidence_ids=c.evidence_ids,
                    statement=c.text if c.kind in ("narrative", "excerpt", "analysis") else "",
                )
                for c in claims
            ],
            gaps=research.gaps[:8],
        )
        verification_question = (
            research.followup.get("prior_question")
            if research.followup and research.followup.get("reused_prior")
            else None
        ) or ("股票怎么样？" if research.mode == "company_report" else research.question)
        _, findings = verify_submission(
            submission,
            tools,
            verification_question,
            focus_ticker=package.focus_ticker,
            allow_excerpts=True,
        )
        for finding in findings:
            if finding == "Narrative verification unavailable; descriptive claims were withheld":
                continue
            check("claim_rules", False, finding)
        for claim in claims:
            marker = {
                "fact": "deterministic",
                "calculation": "deterministic",
                "market": "deterministic",
                "valuation": "deterministic",
                "excerpt": "exact_source_excerpt",
                "narrative": "semantic_review",
                "analysis": "inference_review",
            }[claim.kind]
            check("claim_type", claim.verification == marker, "核验类别须与结论类型一致。")
            if claim.kind in ("fact", "calculation", "market", "valuation"):
                item = tools.ledger.get(claim.evidence_ids[0])
                check(
                    "numeric_text",
                    item is not None and claim.text == render_numeric(item, package.domain, language=research.language, rendering=claim.rendering),
                    "数字句须与重算后的代码渲染结果逐字一致。",
                )
                numeric_count += 1
            elif claim.kind in ("narrative", "analysis"):
                check("claim_rendering", claim.rendering is None, "非数字结论不能使用数字渲染版本。")
                narrative_count += 1
        if narrative_count:
            checks.append(
                {
                    "code": "semantic_entailment",
                    "status": "not_checked",
                    "detail": f"{narrative_count} 项叙述或分析的完整语义支持关系未重新调用模型或人工审查。",
                }
            )
        if source_cache is not None:
            _check_raw_sources(ledger, filings, source_cache, check, raw, package.domain)
            if raw["missing"]:
                checks.append(
                    {
                        "code": "raw_source_missing",
                        "status": "not_checked",
                        "detail": f"{raw['missing']} 项原始缓存缺失，不能独立核验对应原文或 XBRL。",
                    }
                )
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError, OSError) as exc:
        check(
            "package_structure",
            False,
            f"研究包格式或证据结构无效：{type(exc).__name__}: {str(exc)[:500]}",
        )
    failed = sum(c["status"] == "fail" for c in checks)
    return {
        "status": "invalid" if failed else "consistent",
        "checks_passed": sum(c["status"] == "pass" for c in checks),
        "checks_failed": failed,
        "source_validation": "not_requested"
        if corpus is None and market_cache is None
        else "not_checked"
        if not source_total
        else "matched"
        if source_matches == source_total
        else "mismatch",
        "source_evidence_checked": source_total,
        "numeric_claims_recomputed": numeric_count,
        "narrative_claims_not_rechecked": narrative_count,
        "raw_sources": {"requested": source_cache is not None, **raw},
        "market_sources": market_sources,
        "valuation_sources": valuation_sources,
        "checks": checks,
        "limitations": [
            "consistent 表示已执行的结构、数字和一致性检查通过，不等于所有叙述真实。",
            "无本地证据库或原始缓存时，包内校验和不能证明来源；整体改写后重算校验和仍可自洽。",
            "本地证据库和缓存由调用者指定，未在线确认 SEC 内容，也未验证数字以外的完整因果含义。",
        ],
    }


def _check_raw_sources(ledger, filings, cache, check, counts, domain):
    from .ifrs_sources import extract_ifrs_facts
    from .ingestion import extract_cash_facts, extract_passages

    inline = {}

    for filing in filings.values():
        path = cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")
        if not path.is_file():
            counts["missing"] += 1
            continue
        counts["filings_checked"] += 1
        content = path.read_bytes()
        check(
            "raw_filing_hash",
            hashlib.sha256(content).hexdigest() == filing.content_sha256,
            "与本地原始 SEC 申报字节的 SHA256 比较。",
            filing.id,
        )
        if filing.form.startswith(("8-K", "6-K")):
            from .updates import verify_update_metadata

            metadata_matches = verify_update_metadata(cache, filing)
            if metadata_matches is None:
                counts["missing"] += 1
            else:
                check(
                    "raw_update_metadata",
                    metadata_matches,
                    "从保留的 SEC 申报索引核对公开日、SEC 报告标注日期、主体与 accession；附件另核对原申报链接。",
                    filing.id,
                )
        cash_used = [
            e
            for e in ledger.values()
            if e["kind"] == "fact"
            and e["filing_id"] == filing.id
            and e["locator"].startswith("inline-xbrl:")
        ]
        if cash_used:
            inline.update(
                {
                    f.id: f.model_dump(mode="json")
                    for f in extract_cash_facts(content, filing, domain)
                }
            )
            for item in cash_used:
                counts["facts_checked"] += 1
                expected = inline.get(item["id"])
                observed = {k: v for k, v in item.items() if k not in ("kind", "filing")}
                check(
                    "raw_inline_xbrl_fact",
                    expected == observed,
                    "从绑定原始 SEC HTML 重提取现金流事实，核对概念、实体、单位、期间、数值和定位。",
                    item["id"],
                )
        native_used = [
            e
            for e in ledger.values()
            if e["kind"] == "fact"
            and e["filing_id"] == filing.id
            and e["locator"].startswith(("inline-ifrs:", "inline-us-gaap:"))
        ]
        if native_used:
            extracted_native = {
                f.id: f.model_dump(mode="json") for f in extract_ifrs_facts(content, filing, domain)
            }
            for item in native_used:
                counts["facts_checked"] += 1
                check(
                    "raw_inline_ifrs_fact" if item["locator"].startswith("inline-ifrs:") else "raw_inline_us_gaap_fact",
                    extracted_native.get(item["id"])
                    == {k: v for k, v in item.items() if k not in ("kind", "filing")},
                    "从绑定原始 SEC HTML 重提取登记的原币年度 XBRL 事实，核对实体、合并或登记的 ADS 股份类别、概念、单位、期间、精度及定位。",
                    item["id"],
                )
        used = [
            e for e in ledger.values() if e["kind"] == "passage" and e["filing_id"] == filing.id
        ]
        if used:
            extracted = {
                p.id: p.model_dump(mode="json")
                for p in extract_passages(content, filing, include_tables=True)
            }
            for item in used:
                expected = extracted.get(item["id"])
                observed = {k: v for k, v in item.items() if k not in ("kind", "filing")}
                # Legacy records omitted this optional default. Preserve exact
                # text/locator and real table metadata checks without rewriting packages.
                observed.setdefault("table_context", None)
                check(
                    "raw_passage",
                    expected == observed,
                    "从原始申报重新提取段落，核对文本与定位。",
                    item["id"],
                )
    documents = {}
    for key, item in ledger.items():
        if item["kind"] != "fact":
            continue
        if item["locator"].startswith(("inline-xbrl:", "inline-ifrs:", "inline-us-gaap:")):
            if item["id"] not in inline:
                filing = filings[item["filing_id"]]
                path = cache / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")
                if not path.exists():
                    counts["missing"] += 1
            continue
        url = item["source_url"]
        try:
            path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")
            match = re.fullmatch(
                r"facts\.us-gaap\.([A-Za-z0-9]+)\.units\.([A-Za-z0-9/]+)\[(\d+)\]; accession=([^;]+)(?:; source_sha256=([a-f0-9]{64}))?",
                item["locator"],
            )
            if match is None:
                if not path.is_file():
                    counts["missing"] += 1
                    continue
                raise ValueError("Unknown XBRL locator")
            concept, unit, offset, accession, version = match.groups()
            path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".cache")
            paths = [cache / "raw" / (version + ".cache"), path] if version else [path]
            if (
                version
                and not paths[0].is_file()
                and (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != version)
            ):
                counts["missing"] += 1
                continue
            # Legacy packages predate response-version locators. Their exact original
            # offset/row can be matched against a retained response, never fuzzy text.
            if not version and (cache / "raw").is_dir():
                paths.extend(sorted((cache / "raw").glob("*.cache")))
            paths = [p for p in paths if p.is_file()]
            if not paths:
                counts["missing"] += 1
                continue
            counts["facts_checked"] += 1
            matches = False
            for candidate in paths:
                try:
                    if candidate not in documents:
                        content = candidate.read_bytes()
                        content_hash = hashlib.sha256(content).hexdigest()
                        if candidate.parent.name == "raw" and content_hash != candidate.stem:
                            continue
                        documents[candidate] = (
                            content_hash,
                            json.loads(content, parse_float=Decimal),
                        )
                    content_hash, document = documents[candidate]
                    if version and content_hash != version:
                        continue
                    row = document["facts"]["us-gaap"][concept]["units"][unit][int(offset)]
                    matches = (
                        item["concept"] == "us-gaap:" + concept
                        and item["unit"] == unit
                        and item["filing"]["accession"] == accession == row["accn"]
                        and row["form"] == item["filing"]["form"]
                        and row.get("start") == item.get("start")
                        and row["end"] == item["end"]
                        and Decimal(str(row["val"])) == Decimal(item["value"])
                    )
                    if matches:
                        break
                except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
                    continue
            check(
                "raw_xbrl_fact",
                matches,
                "按定位读取本地 CompanyFacts 原记录或保留的响应版本，核对数值、单位、期间和 accession。",
                key,
            )
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
            check("raw_xbrl_fact", False, "本地 CompanyFacts 无法支持该定位与数值。", key)
