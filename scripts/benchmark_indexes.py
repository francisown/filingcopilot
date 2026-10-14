"""Synthetic SQL-only comparison; does not measure end-to-end RAG throughput."""

import json
import sqlite3
import statistics
import tempfile
import time
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus


def measure(db, sql, arguments):
    samples, count = [], None
    for _ in range(5):
        started = time.perf_counter()
        count = db.execute(sql, arguments).fetchone()[0]
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "rows": count,
        "median_ms": round(statistics.median(samples), 3),
        "plan": [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql, arguments)],
    }


def main():
    with tempfile.TemporaryDirectory(prefix="filingcopilot-index-") as directory:
        corpus = Corpus(Path(directory))
        with corpus.connect() as db:
            for index in range(1000):
                ticker, year = f"T{index % 20:02d}", 2015 + (index // 20) % 10
                filing = {"period_end": f"{year}-12-31"}
                db.execute(
                    "INSERT INTO filings VALUES(?,?,?,?,?,?)",
                    (
                        str(index),
                        ticker,
                        f"{year + 1}-02-01",
                        json.dumps(filing),
                        filing["period_end"],
                        year,
                    ),
                )
            db.executemany(
                "INSERT INTO passages VALUES(?,?,?,?)",
                (
                    (str(index), str(index // 100), f"T{(index // 100) % 20:02d}", "{}")
                    for index in range(100000)
                ),
            )
            db.executemany(
                "INSERT INTO facts VALUES(?,?,?,?,?,?)",
                (
                    (
                        str(index),
                        str(index // 100),
                        f"T{(index // 100) % 20:02d}",
                        f"metric{index % 8}",
                        2015 + ((index // 100) // 20) % 10,
                        "{}",
                    )
                    for index in range(100000)
                ),
            )
            db.execute("ANALYZE")
        with corpus.connect() as db:
            baseline = measure(
                db,
                "SELECT count(*) FROM passages p JOIN filings d ON d.id=p.filing_id "
                "WHERE d.filed<=? AND (? IS NULL OR p.ticker=?) "
                "AND (? IS NULL OR substr(json_extract(d.payload,'$.period_end'),1,4)=?)",
                ("2026-10-08", "T00", "T00", 2020, "2020"),
            )
            scope, arguments = corpus._scope(date(2026, 10, 8), "T00", 2020)
            indexed = measure(
                db,
                "SELECT count(*) FROM passages p JOIN filings d ON d.id=p.filing_id WHERE " + scope,
                arguments,
            )
            fact_sql = "SELECT count(*) FROM facts {} WHERE ticker=? AND metric=? AND fiscal_year=?"
            fact_args = ("T00", "metric0", 2020)
            fact_baseline = measure(db, fact_sql.format("NOT INDEXED"), fact_args)
            fact_indexed = measure(db, fact_sql.format(""), fact_args)
        assert baseline["rows"] == indexed["rows"] > 0
        assert fact_baseline["rows"] == fact_indexed["rows"] > 0
        report = {
            "dataset": "synthetic: 1000 filings, 100000 passages, 100000 facts",
            "scope": "SQL predicates/counts only; timings depend on machine/cache",
            "sqlite_version": sqlite3.sqlite_version,
            "passages": {"previous_predicate": baseline, "indexed_predicate": indexed},
            "facts": {"table_scan": fact_baseline, "compound_index": fact_indexed},
        }
    target = Path(__file__).resolve().parents[1] / "artifacts" / "index-benchmark.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
