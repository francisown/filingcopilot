"""Score a monitoring prediction set against an event-level gold file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from filingcopilot.monitoring.evaluation import score_events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predicted", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-recall", type=float)
    parser.add_argument("--min-recall-lower", type=float)
    parser.add_argument("--max-fp-rate", type=float)
    parser.add_argument("--max-no-event-fp", type=float)
    args = parser.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8-sig"))
    predicted = json.loads(args.predicted.read_text(encoding="utf-8-sig"))
    report = score_events(gold, predicted)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content)

    if args.min_recall is not None and report["overall"]["recall"] < args.min_recall:
        raise SystemExit(1)
    if args.min_recall_lower is not None:
        lower = report["overall"]["recall_ci"][0]
        if lower is None or lower < args.min_recall_lower:
            raise SystemExit(1)
    if args.max_fp_rate is not None:
        predicted_count = report["predicted_events"]
        fp_rate = (
            len(report["false_positives"]) / predicted_count
            if predicted_count
            else 0.0
        )
        if fp_rate > args.max_fp_rate:
            raise SystemExit(1)
    if (
        args.max_no_event_fp is not None
        and report["no_event_fp_rate"] is not None
        and report["no_event_fp_rate"] > args.max_no_event_fp
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
