"""Repeatable public-snapshot checks; this is not an LLM accuracy benchmark."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

from .audit import audit_package, create_package, digest
from .models import ResearchAnswer, ResearchRequest
from .service import FilingCopilot


def evaluate(service: FilingCopilot) -> dict:
    tools = service.tools(date(2026, 10, 8))
    checks = []

    def record(name, passed, observed):
        checks.append({"name": name, "passed": bool(passed), "observed": observed})

    # Fixed USD expectations, independently transcribed from the FY2025 annual statements.
    gold = {
        ("AAPL", "revenue"): "416161000000",
        ("AAPL", "net_income"): "112010000000",
        ("MSFT", "revenue"): "281724000000",
        ("MSFT", "operating_income"): "128528000000",
        ("NVDA", "revenue"): "130497000000",
        ("NVDA", "gross_profit"): "97858000000",
    }
    for (ticker, metric), expected in gold.items():
        result = tools.get_financial_fact(ticker, metric, 2025)
        observed = result.get("evidence", {}).get("value")
        record(f"{ticker} FY2025 {metric}", observed == expected, observed)

    for ticker, metric, expected in (
        ("AAPL", "gross_profit", "46.91"),
        ("MSFT", "operating_income", "45.62"),
        ("NVDA", "gross_profit", "74.99"),
    ):
        left = tools.get_financial_fact(ticker, metric, 2025)
        right = tools.get_financial_fact(ticker, "revenue", 2025)
        calc = tools.calculate("ratio", left["evidence"]["id"], right["evidence"]["id"])
        observed = f"{Decimal(calc['evidence']['value']):.2f}"
        record(f"{ticker} FY2025 {metric}/revenue %", observed == expected, observed)

    current = tools.get_financial_fact("AAPL", "revenue", 2025)["evidence"]
    previous = tools.get_financial_fact("AAPL", "revenue", 2024)["evidence"]
    growth = tools.calculate("growth", current["id"], previous["id"])["evidence"]
    observed = f"{Decimal(growth['value']):.2f}"
    record("AAPL FY2025 revenue growth %", observed == "6.43", observed)

    for ticker, cutoff in (("AAPL", "2025-10-30"), ("MSFT", "2025-07-29"), ("NVDA", "2025-02-25")):
        before = service.tools(date.fromisoformat(cutoff))
        result = before.get_financial_fact(ticker, "revenue", 2025)
        record(f"{ticker} no FY2025 fact before publication", not result["found"], result["found"])

    for ticker, query, terms in (
        ("AAPL", "manufacturing partners supply chain risk", ("manufactur", "supply")),
        ("MSFT", "Azure cloud revenue growth driven consumption", ("azure", "cloud")),
        ("NVDA", "export restrictions China licensing risk", ("export", "china")),
    ):
        result = tools.search_passages(query, ticker, 5, 2025)
        hits = result["hits"]
        topic_hit = any(any(t in hit["passage"]["text"].lower() for t in terms) for hit in hits)
        scope_ok = all(
            hit["passage"]["ticker"] == ticker
            and tools.ledger[hit["passage"]["id"]]["filing"]["period_end"][:4] == "2025"
            for hit in hits
        )
        record(
            f"{ticker} scoped narrative retrieval top5",
            topic_hit and scope_ok,
            {
                "backend": result["backend"],
                "hits": len(hits),
                "topic_hit": topic_hit,
                "scope_ok": scope_ok,
                "evidence_ids": [h["passage"]["id"] for h in hits],
            },
        )
    return {
        "dataset": "bundled SEC annual snapshot; knowledge date 2026-10-08",
        "scope": "numeric gold, arithmetic, temporal exclusion, retrieval smoke checks; "
        "not a generated-answer accuracy estimate",
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "checks": checks,
    }


def evaluate_integrity(service: FilingCopilot) -> dict:
    """Synthetic adversarial package checks, not generated-answer or competitor accuracy."""
    cutoff = date(2026, 10, 8)
    checks = []

    def package(question, ticker="AAPL", as_of=cutoff):
        request = ResearchRequest(question=question, ticker=ticker, as_of=as_of, mode="reference")
        answer = service.reference(request, service.tools(as_of))
        return create_package(answer, service.domain, ticker)

    def record(name, candidate, expected="invalid", *, reseal=True, condition=None, anchored=True):
        if reseal:
            candidate["manifest"]["payload_sha256"] = digest(
                {k: v for k, v in candidate.items() if k != "manifest"}
            )
        report = audit_package(
            candidate,
            corpus=service.corpus if anchored else None,
            trusted_domain=service.domain if anchored else None,
        )
        checks.append(
            {
                "name": name,
                "expected_status": expected,
                "observed_status": report["status"],
                "passed": report["status"] == expected
                and (condition(report) if condition else True),
                "failed_checks": [c["code"] for c in report["checks"] if c["status"] == "fail"],
                "source_validation": report["source_validation"],
                "narrative_claims_not_rechecked": report["narrative_claims_not_rechecked"],
            }
        )

    base = package("苹果 FY2025 营收同比和毛利率")
    fact_only = package("苹果 FY2025 营收")
    for ticker, question in (
        ("AAPL", "苹果 FY2025 营收同比和毛利率"),
        ("MSFT", "微软 FY2025 营业利润率"),
        ("NVDA", "英伟达 FY2025 毛利率"),
    ):
        record(f"valid {ticker} numeric package", package(question, ticker), "consistent")
    record(
        "valid standalone package without source authentication",
        deepcopy(base),
        "consistent",
        anchored=False,
        condition=lambda r: r["source_validation"] == "not_requested",
    )
    before = package("苹果 FY2025 营收", as_of=date(2025, 10, 30))
    record(
        "valid abstention before publication",
        before,
        "consistent",
        condition=lambda r: before["research"]["status"] == "abstained",
    )

    for name in (
        "checksum",
        "rendered amount",
        "calculated value",
        "formula",
        "input ID",
        "requested company",
        "requested fiscal year",
        "knowledge date",
        "missing citation",
        "missing evidence",
        "duplicate index",
        "false complete status",
        "currency",
        "quarter",
        "fact year",
        "claim verification marker",
    ):
        candidate = deepcopy(fact_only if name in ("currency", "quarter", "fact year") else base)
        research = candidate["research"]
        calc = next((e for e in research["evidence"] if e["kind"] == "calculation"), None)
        if name == "checksum":
            research["question"] += "?"
        elif name == "rendered amount":
            research["answer"][0]["text"] = "金额是 999 亿美元。"
        elif name == "calculated value":
            calc["value"] = "999"
        elif name == "formula":
            calc["formula"] = "left + right"
        elif name == "input ID":
            calc["input_ids"][0] = "invented"
        elif name == "requested company":
            research["question"] = "微软 FY2025 营收同比和毛利率"
        elif name == "requested fiscal year":
            research["question"] = "苹果 FY2024 营收同比和毛利率"
        elif name == "knowledge date":
            research["as_of"] = "2020-01-01"
        elif name == "missing citation":
            research["answer"][0]["evidence_ids"] = ["invented"]
        elif name == "missing evidence":
            research["evidence"] = []
        elif name == "duplicate index":
            research["answer"][1]["index"] = research["answer"][0]["index"]
        elif name == "false complete status":
            research["answer"] = []
            research["status"] = "answered"
        elif name == "currency":
            research["evidence"][0]["unit"] = "EUR"
        elif name == "quarter":
            research["evidence"][0]["start"] = "2025-07-01"
        elif name == "fact year":
            research["evidence"][0]["fiscal_year"] = 2024
        else:
            research["answer"][0]["verification"] = "semantic_review"
        record(f"reject {name}", candidate, reseal=name != "checksum")

    rewritten = deepcopy(fact_only)
    rewritten["research"]["evidence"][0]["value"] = "100000000000"
    from .verification import render_numeric

    rewritten["research"]["answer"][0]["text"] = render_numeric(
        rewritten["research"]["evidence"][0], service.domain
    )
    record(
        "rewritten values remain self-consistent but unauthenticated",
        deepcopy(rewritten),
        "consistent",
        anchored=False,
    )
    record("independent corpus rejects rewritten values despite checksum", rewritten)

    passage = service.corpus.passages(cutoff, "AAPL", 2025)[0]
    evidence = service.corpus.evidence(passage.id, cutoff)
    excerpt = ResearchAnswer(
        id="evaluation-excerpt",
        question="苹果 FY2025 原文",
        as_of=cutoff,
        mode="reference",
        status="partial",
        answer=[
            {
                "index": 0,
                "kind": "excerpt",
                "text": passage.text,
                "evidence_ids": [passage.id],
                "verification": "exact_source_excerpt",
            }
        ],
        gaps=["原文摘录，不生成解释。"],
        evidence=[evidence],
        trace=[],
        usage={"model_calls": 0},
        elapsed_ms=0,
    )
    original = create_package(excerpt, service.domain, "AAPL")
    record("valid exact excerpt", deepcopy(original), "consistent")
    modified = deepcopy(original)
    modified["research"]["answer"][0]["text"] = "This invented excerpt is not in the filing."
    record("reject rewritten excerpt", modified)
    narrative = deepcopy(original)
    narrative["research"]["answer"][0].update(
        kind="narrative", text="公司披露了相关信息。", verification="semantic_review"
    )
    record(
        "model review marker remains explicitly unchecked offline",
        narrative,
        "consistent",
        condition=lambda r: r["narrative_claims_not_rechecked"] == 1,
    )

    return {
        "dataset": "bundled SEC snapshot with explicit synthetic package mutations",
        "scope": "offline consistency and source-boundary challenge suite; no live LLM calls, "
        "no narrative truth score and no competitor accuracy comparison",
        "corpus_filings_sha256": digest(service.corpus.catalog(cutoff)),
        "domain_sha256": digest(service.domain.model_dump(mode="json")),
        "model_calls": 0,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "synthetic_invalid_cases": sum(c["expected_status"] == "invalid" for c in checks),
        "synthetic_invalid_accepted": sum(
            c["expected_status"] == "invalid" and c["observed_status"] == "consistent"
            for c in checks
        ),
        "valid_consistency_cases_rejected": sum(
            c["expected_status"] == "consistent" and c["observed_status"] == "invalid"
            for c in checks
        ),
        "checks": checks,
    }
