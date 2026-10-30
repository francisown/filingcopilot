"""Run the versioned semantic set through the real research and review pipeline.

This runner deliberately records failures instead of converting exceptions into
good scores.  The service's deterministic verifier supplies the typed/citation
signals; the configured review model is recorded separately so the report does
not pretend that a model judgment is a factual ground truth.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import tempfile
import threading
import time
import traceback
from pathlib import Path

from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    """Readers see either the previous complete checkpoint or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest(gold: dict, settings: Settings, workers: int, limit: int | None, as_of: str) -> dict:
    # Explicit allowlist: credentials and provider URLs never enter public artifacts.
    configuration = {
        name: getattr(settings, name)
        for name in (
            "model", "review_model", "research_timeout", "model_timeout", "min_model_interval",
            "max_rounds", "max_tool_calls", "model_max_tokens", "review_max_tokens",
            "model_attempts", "retry_base_delay", "retry_max_delay", "refresh_timeout",
            "embedding_mode", "embedding_model", "rerank_mode",
            "model_input_price_per_million", "model_output_price_per_million",
        )
    }
    configuration["provider_sha256"] = hashlib.sha256(settings.api_base.encode()).hexdigest()
    configuration["home"] = str(settings.home.resolve())
    configuration["workers"] = workers
    for name in ("domain_config", "market_config"):
        path = getattr(settings, name)
        configuration[name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
    root = Path(__file__).resolve().parents[1]
    sources = sorted((root / "src" / "filingcopilot").rglob("*.py")) + [
        root / "scripts" / "run_semantic_evaluation.py", root / "scripts" / "evaluate_semantic_set.py",
    ]
    source_hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    context = {
        "schema": "filingcopilot.semantic-run-context/v1",
        "gold_sha256": _digest(gold),
        "item_ids": [item["id"] for item in (gold["items"][:limit] if limit else gold["items"])],
        "configuration": configuration,
        "as_of": as_of,
        "source_sha256": _digest(source_hashes),
    }
    return {**context, "fingerprint": _digest(context)}


def _resume_rows(path: Path, context: dict, items: list[dict]) -> list[dict]:
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    if not manifest_path.exists():
        raise ValueError("Resume requires the original checkpoint manifest; legacy rows cannot prove their configuration")
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    if saved != context:
        raise ValueError("Resume configuration, source or gold differs from the original run")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Checkpoint must be a list of captured rows")
    expected = {item["id"]: item for item in items}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("Checkpoint contains an invalid row")
        item_id = row["id"]
        if item_id in seen or item_id not in expected:
            raise ValueError("Checkpoint contains duplicate or unknown question IDs")
        if row.get("evaluation_fingerprint") != context["fingerprint"]:
            raise ValueError("Checkpoint row lacks matching run provenance")
        if any(row.get(name) != expected[item_id][name] for name in ("question", "ticker", "category", "expected_behavior")):
            raise ValueError("Checkpoint row does not match its frozen question")
        if any(name not in row for name in ("status", "unsupported_claims", "numeric_errors", "citation_supported")):
            raise ValueError("Checkpoint row is incomplete")
        seen.add(item_id)
    return rows


def _safe_message(exc: Exception, settings: Settings) -> str:
    message = str(exc).strip() or type(exc).__name__
    for secret in (settings.api_key, settings.twelve_data_api_key, settings.api_base):
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _answer_text(answer) -> str:
    return "\n".join(str(claim.get("text", "")) for claim in answer)


def _row(item: dict, result, elapsed_ms: int, review_model: str = "") -> dict:
    evidence = {entry.get("id") for entry in result.evidence}
    claims = result.answer
    cited = bool(claims) and all(
        claim.get("evidence_ids") and set(claim.get("evidence_ids", [])) <= evidence
        for claim in claims
    )
    unsupported = sum(
        not claim.get("evidence_ids")
        or not set(claim.get("evidence_ids", [])).issubset(evidence)
        for claim in claims
    )
    findings = [
        finding
        for step in result.trace
        if step.get("stage") == "verify"
        for finding in step.get("findings", [])
    ]
    numeric_errors = sum(
        isinstance(finding, dict)
        and
        finding.get("code") in {"numeric_text", "calculation", "annual_fact"}
        and finding.get("status") == "fail"
        for finding in findings
    )
    expected = item["expected_behavior"]
    refused = result.status in {"refused", "abstained"}
    gap_text = " ".join(str(gap) for gap in result.gaps)
    timed_out = (
        result.usage.get("budget_status") == "expired"
        or "时间预算已用完" in gap_text
        or "时间预算结束" in gap_text
    )
    # A provider failure is not the system choosing to refuse. Counting it as a
    # wrong refusal turns an infrastructure outage into a quality signal.
    aborted = "Model request failed" in gap_text
    reasonable_refusal = (
        expected == "reasonable_refusal_with_actionable_gap"
        and refused
        and bool(result.gaps)
        and not timed_out
        and not aborted
    )
    wrong_refusal = (
        refused
        and expected != "reasonable_refusal_with_actionable_gap"
        and not timed_out
        and not aborted
    )
    trace = result.trace
    tool_steps = [
        step
        for step in trace
        if step.get("tool") or step.get("stage") == "tool"
    ]
    model_steps = [
        step
        for step in trace
        if step.get("stage") in {"reason", "model", "model_request", "review", "semantic_review"}
    ]
    tool_names = sorted(
        {
            str(step.get("tool") or step.get("stage"))
            for step in tool_steps
        }
    )
    direct_text_rounds = sum(
        1
        for step in model_steps
        if step.get("finish_reason") == "stop" and not step.get("tool_calls")
    )
    author_ms = round(
        sum(float(step.get("duration_ms") or 0) for step in trace if step.get("stage") in {"reason", "model"}),
        3,
    )
    review_ms = round(
        sum(float(step.get("duration_ms") or 0) for step in trace if step.get("stage") in {"review", "semantic_review"}),
        3,
    )
    return {
        "id": item["id"],
        "category": item["category"],
        "ticker": item["ticker"],
        "question": item["question"],
        "expected_behavior": expected,
        "status": result.status,
        "answer": _answer_text(claims),
        "evidence_ids": sorted({eid for claim in claims for eid in claim.get("evidence_ids", [])}),
        "answer_claims": len(claims),
        "has_citation": bool(claims),
        "citation_supported": cited,
        "unsupported_claims": int(unsupported),
        "numeric_errors": int(numeric_errors),
        "verification_findings": len(findings),
        "budget_timeout": timed_out,
        "reasonable_refusal": reasonable_refusal,
        "wrong_refusal": wrong_refusal,
        "gaps": result.gaps,
        "latency_ms": elapsed_ms,
        "cost_usd": result.usage.get("actual_cost_usd"),
        "run_id": result.id,
        "model_calls": result.usage.get("model_calls", 0),
        "reported_tokens": result.usage.get("total_tokens"),
        "prompt_tokens": result.usage.get("prompt_tokens"),
        "completion_tokens": result.usage.get("completion_tokens"),
        "review_model": review_model or os.getenv("FC_REVIEW_MODEL", ""),
        "trace_summary": {
            "tool_calls": len(tool_steps),
            "tool_names": tool_names,
            "model_steps": len(model_steps),
            "direct_text_rounds": direct_text_rounds,
            "author_ms": author_ms,
            "review_ms": review_ms,
            "finish_reasons": [
                step.get("finish_reason")
                for step in model_steps
                if step.get("finish_reason")
            ],
        },
    }


def run(
    gold: dict,
    *,
    limit: int | None = None,
    checkpoint_path: Path | None = None,
    workers: int = 1,
    resume_path: Path | None = None,
    settings: Settings | None = None,
    as_of: str = "2026-10-08",
) -> tuple[list[dict], list[dict]]:
    settings = settings or Settings.from_env()
    if not settings.llm_ready:
        raise RuntimeError("FC_API_BASE, FC_API_KEY and FC_MODEL are required for semantic evaluation")
    if not settings.review_model or settings.review_model == settings.model:
        raise RuntimeError("FC_REVIEW_MODEL must be configured and differ from FC_MODEL")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    ids = [item["id"] for item in gold["items"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Gold question IDs must be unique")
    context = _manifest(gold, settings, workers, limit, as_of)
    all_items = gold["items"][:limit] if limit else gold["items"]
    rows = _resume_rows(resume_path, context, all_items) if resume_path else []
    if checkpoint_path:
        if checkpoint_path.exists() and not (resume_path and checkpoint_path.resolve() == resume_path.resolve()):
            raise ValueError("Checkpoint already exists; resume it explicitly or choose a fresh path")
        _atomic_json(checkpoint_path.with_suffix(checkpoint_path.suffix + ".manifest.json"), context)
        _atomic_json(checkpoint_path, rows)
    failures: list[dict] = []
    completed_ids = {row["id"] for row in rows}
    items = [item for item in all_items if item["id"] not in completed_ids]
    local = threading.local()

    def evaluate_one(item: dict) -> dict:
        if not hasattr(local, "service"):
            local.service = FilingCopilot(settings)
        started = time.perf_counter()
        result = None
        try:
            result = local.service.research(
                ResearchRequest(
                    question=item["question"],
                    ticker=item["ticker"],
                    as_of=as_of,
                    mode="agent",
                    language=item.get("language", "en"),
                )
            )
            row = _row(
                item,
                result,
                round((time.perf_counter() - started) * 1000),
                settings.review_model,
            )
        except ValueError as exc:  # capability gates are product refusals, not harness failures
            message = _safe_message(exc, settings)
            expected = item["expected_behavior"]
            row = {
                "id": item["id"],
                "category": item["category"],
                "ticker": item["ticker"],
                "question": item["question"],
                "expected_behavior": expected,
                "status": "abstained",
                "answer": "",
                "evidence_ids": [],
                "answer_claims": 0,
                "has_citation": False,
                "citation_supported": False,
                "unsupported_claims": 0,
                "numeric_errors": 0,
                "budget_timeout": False,
                "reasonable_refusal": expected == "reasonable_refusal_with_actionable_gap",
                "wrong_refusal": expected != "reasonable_refusal_with_actionable_gap",
                "gaps": [message],
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "cost_usd": None,
                "model_calls": 0,
                "review_model": settings.review_model,
                "run_id": None,
                "error_phase": "capability_gate",
                "error_message": message,
            }
        except Exception as exc:  # keep the failure visible and continue the set
            message = _safe_message(exc, settings)
            row = {
                "id": item["id"],
                "category": item["category"],
                "ticker": item["ticker"],
                "question": item["question"],
                "expected_behavior": item["expected_behavior"],
                "status": "runner_error",
                "answer": "",
                "evidence_ids": [],
                "answer_claims": 0,
                "has_citation": False,
                "citation_supported": False,
                "unsupported_claims": 0,
                "numeric_errors": 0,
                "budget_timeout": False,
                "reasonable_refusal": False,
                # A runner failure is a harness failure, not a model refusal.
                "wrong_refusal": False,
                "gaps": [f"runner exception: {type(exc).__name__}: {message}"],
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "cost_usd": None,
                "model_calls": 0,
                "review_model": settings.review_model,
                "run_id": result.id if result else None,
                "error_phase": "capture" if result else "research",
                "error_message": message,
                "error_frames": [
                    {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(exc.__traceback__)[-4:]
                ],
            }
        row["evaluation_fingerprint"] = context["fingerprint"]
        return row

    def record(index: int, row: dict) -> None:
        rows.append(row)
        if checkpoint_path:
            _atomic_json(checkpoint_path, rows)
        print(f"[{len(rows)}/{len(all_items)}] {row['id']} {row['status']} {row['latency_ms']}ms", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(evaluate_one, item): index for index, item in enumerate(items, 1)}
        for future in concurrent.futures.as_completed(pending):
            record(pending[future], future.result())
    rows.sort(key=lambda row: row["id"])
    failures = [row for row in rows if row["status"] != "answered" or row["unsupported_claims"] or row["numeric_errors"] or not row["citation_supported"]]
    failures.sort(key=lambda row: row["id"])
    if checkpoint_path:
        _atomic_json(checkpoint_path, rows)
    return rows, failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("evaluation/semantic_questions_v1.json"))
    parser.add_argument("--results", type=Path, default=Path("artifacts/semantic-evaluation-results-2026-10-11.json"))
    parser.add_argument("--failures", type=Path, default=Path("artifacts/semantic-evaluation-failures-2026-10-11.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/semantic-evaluation-2026-10-11.json"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--checkpoint", type=Path, default=Path("artifacts/semantic-evaluation-checkpoint-2026-10-11.json"))
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Concurrent workers. Keep at 1: two workers on one corpus produced "
            "'attempt to write a readonly database' rows in the 140-question run "
            "of 2026-10-28."
        ),
    )
    parser.add_argument("--resume", type=Path, help="Reuse captured rows from this exact configuration; never reruns them")
    parser.add_argument("--as-of", default="2026-10-08")
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    settings = Settings.from_env()
    rows, failures = run(gold, limit=args.limit, checkpoint_path=args.checkpoint, workers=args.workers, resume_path=args.resume, settings=settings, as_of=args.as_of)
    for path in (args.results, args.failures, args.output):
        path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(args.results, rows)
    _atomic_json(args.failures, failures)
    # Reuse the published scorer so metric definitions cannot drift from the CLI.
    from evaluate_semantic_set import evaluate

    report = {
        "schema": "filingcopilot.semantic-evaluation/v1",
        "gold": {"path": str(args.gold), "version": gold.get("version"), "count": len(gold["items"])},
        "status": "scored" if len(rows) == len(gold["items"]) else "partial_set",
        "runner": "scripts/run_semantic_evaluation.py",
        "review_model": settings.review_model,
        "research_model": settings.model,
        "run_configuration": {
            "research_timeout_seconds": settings.research_timeout,
            "model_timeout_seconds": settings.model_timeout,
            "min_model_interval_seconds": settings.min_model_interval,
            "max_rounds": settings.max_rounds,
            "max_tool_calls": settings.max_tool_calls,
            "workers": args.workers,
            "as_of": args.as_of,
        },
        "run_context": _manifest(gold, settings, args.workers, args.limit, args.as_of),
        "metrics": evaluate(gold, rows),
        "failure_count": len(failures),
        "failure_file": str(args.failures),
        "notes": [
            "Rows were generated by the live research and configured independent review pipeline.",
            "Typed/citation fields come from deterministic verification; semantic quality still needs human review.",
            "Citation support here means valid observed evidence references, not human-verified entailment.",
            "Verification findings describe withheld draft claims; they are not counted as errors in a delivered answer.",
            "No score is awarded for shortening an answer below its gold minimum.",
        ],
    }
    _atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
