"""Evaluate a frozen source tree with shared transport configuration and full receipts.

Run each arm in its own process and home. This supplies identical provider headers
through the existing gateway injection seam, so source compatibility and research
quality can be reported separately. It does not modify either implementation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path


def digest_files(root: Path, pattern: str) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob(pattern))
        if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--as-of", default="2026-10-08")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be between one and four per arm")
    source = args.source.resolve()
    sys.path.insert(0, str(source / "src"))
    import httpx
    from dotenv import load_dotenv

    import filingcopilot
    from filingcopilot.agent_gateway import OpenAIGateway
    from filingcopilot.execution import ResearchControl, current_control
    from filingcopilot.models import ResearchRequest
    from filingcopilot.service import FilingCopilot
    from filingcopilot.settings import Settings

    assert Path(filingcopilot.__file__).resolve().is_relative_to(source)
    load_dotenv(args.env_file, override=True)
    settings = replace(Settings.from_env(), home=args.home.resolve())
    headers = json.loads(os.getenv("FC_EXTRA_HEADERS", "{}"))
    if os.getenv("FC_SESSION_ID"):
        headers.setdefault("x-opencode-session", os.environ["FC_SESSION_ID"])
    secrets = [settings.api_key, settings.twelve_data_api_key, settings.api_base, *headers.values()]

    def scrub(value):
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(str(secret), "[redacted]")
            return value
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    def write(path, data):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(scrub(data), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    spec = importlib.util.spec_from_file_location(
        "arm_runner", source / "scripts/run_semantic_evaluation.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    items = gold["items"]
    assert len({item["id"] for item in items}) == len(items)
    args.output.mkdir(parents=True, exist_ok=True)
    context = runner._manifest(gold, settings, args.workers, None, args.as_of)
    context.update(
        harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_files=digest_files(source / "src", "*"),
        transport="Identical configured headers supplied to both arms via http_client",
        request_language="Each frozen item's declared language",
        network="Existing source and market loaders; no synthetic market failure injected",
    )
    # Bytecode is runtime state, not source provenance.
    context["source_files"] = {
        k: v for k, v in context["source_files"].items() if "__pycache__" not in k
    }
    manifest = args.output / "manifest.json"
    if manifest.exists():
        if not args.resume or json.loads(manifest.read_text(encoding="utf-8")) != scrub(context):
            raise ValueError(
                "Existing run requires --resume with identical source, questions and configuration"
            )
    else:
        write(manifest, context)
    rows = []
    checkpoint = args.output / "results.json"
    if checkpoint.exists():
        if not args.resume:
            raise ValueError("Result checkpoint already exists")
        rows = json.loads(checkpoint.read_text(encoding="utf-8"))
    completed = {r["id"] for r in rows}
    local = threading.local()

    def count_request(request):
        if request.url.path.endswith("/chat/completions"):
            control = current_control()
            if control:
                control.paired_requests += 1

    def count_response(response):
        if response.request.url.path.endswith("/chat/completions"):
            control = current_control()
            if control:
                control.paired_statuses.append(response.status_code)

    def evaluate(item):
        if not hasattr(local, "service"):
            client = httpx.Client(headers=headers)
            gateway = OpenAIGateway(settings, http_client=client)
            client.event_hooks["request"].append(count_request)
            client.event_hooks["response"].append(count_response)
            local.service = FilingCopilot(settings, gateway=gateway)
        control = ResearchControl()
        control.paired_requests, control.paired_statuses = 0, []
        started = time.perf_counter()
        try:
            result = local.service.research(
                ResearchRequest(
                    question=item["question"],
                    ticker=item["ticker"],
                    as_of=args.as_of,
                    mode="agent",
                    language=item["language"],
                    auto_acquire=False,
                ),
                control=control,
            )
            row = runner._row(
                item, result, round((time.perf_counter() - started) * 1000), settings.review_model
            )
            write(args.output / (item["id"] + ".answer.json"), result.model_dump(mode="json"))
        except Exception as exc:
            row = {
                **item,
                "status": "abstained" if isinstance(exc, ValueError) else "runner_error",
                "answer": "",
                "gaps": [runner._safe_message(exc, settings)],
                "error_type": type(exc).__name__,
                "error_phase": "capability_gate" if isinstance(exc, ValueError) else "research",
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_calls": control.usage["model_calls"],
                "budget_timeout": control.expired,
            }
        row.update(http_requests=control.paired_requests, http_statuses=control.paired_statuses)
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(evaluate, item) for item in items if item["id"] not in completed]
        for future in concurrent.futures.as_completed(pending):
            row = future.result()
            rows.append(row)
            write(checkpoint, sorted(rows, key=lambda r: r["id"]))
            print(
                f"[{len(rows)}/{len(items)}] {row['id']} {row['status']} {row['latency_ms']}ms HTTP={row['http_requests']}",
                flush=True,
            )
    # Detect edits made during the run rather than allowing a mixed-source score.
    final_sources = {
        k: v for k, v in digest_files(source / "src", "*").items() if "__pycache__" not in k
    }
    if final_sources != context["source_files"]:
        raise RuntimeError("Frozen source changed during evaluation")
    write(args.output / "complete.json", {"count": len(rows), "source_unchanged": True})


if __name__ == "__main__":
    main()
