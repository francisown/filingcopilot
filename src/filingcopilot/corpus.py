from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from importlib.resources import files
from pathlib import Path

from .models import PARSER_VERSION, ExternalUpdate, Fact, Filing, Passage, StatementFact


def stable_id(prefix: str, *parts: object) -> str:
    value = "|".join(str(p) for p in parts)
    return prefix + "-" + hashlib.sha256(value.encode()).hexdigest()[:20]


class Corpus:
    """Owns versioned evidence and its temporal query rules behind one interface."""

    def __init__(self, home: Path, *, readonly: bool = False):
        self.home = home
        self.readonly = readonly
        # Writes are serialised inside the process. Readers stay lock-free, so a
        # parallel evaluator can read from many threads while the occasional
        # refresh or repair takes this lock instead of racing another writer.
        self._write_lock = threading.RLock()
        self.path = home / "corpus.sqlite3"
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError('Read-only corpus database is missing')
            return  # No mkdir, schema transaction or SQLite header writes.
        home.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS filings (
                    id TEXT PRIMARY KEY, ticker TEXT, filed TEXT, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY, filing_id TEXT, ticker TEXT, metric TEXT,
                    fiscal_year INTEGER, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS statement_facts (
                    id TEXT PRIMARY KEY, filing_id TEXT, ticker TEXT, statement TEXT,
                    metric TEXT, fiscal_year INTEGER, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS passages (
                    id TEXT PRIMARY KEY, filing_id TEXT, ticker TEXT, payload TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
                    id UNINDEXED, text, section, tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP, payload TEXT
                );
                CREATE TABLE IF NOT EXISTS run_packages (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovered_companies (
                    ticker TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    ticker TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    added TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS external_updates (
                    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, published_at TEXT NOT NULL,
                    source_type TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)
            # Serialize schema upgrades across web/CLI/MCP processes. Existing snapshots
            # and run history remain untouched; JSON is parsed only during this backfill.
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(filings)")}
            if "period_end" not in columns:
                db.execute("ALTER TABLE filings ADD COLUMN period_end TEXT")
                db.execute("UPDATE filings SET period_end=json_extract(payload,'$.period_end')")
            if "fiscal_year" not in columns:
                db.execute("ALTER TABLE filings ADD COLUMN fiscal_year INTEGER")
                db.execute("UPDATE filings SET fiscal_year=CAST(substr(period_end,1,4) AS INTEGER)")
            if "form" not in columns:
                db.execute("ALTER TABLE filings ADD COLUMN form TEXT")
                db.execute("UPDATE filings SET form=json_extract(payload,'$.form')")
            for sql in (
                "CREATE INDEX IF NOT EXISTS filings_scope ON filings(ticker,fiscal_year,filed,id)",
                "CREATE INDEX IF NOT EXISTS filings_year ON filings(fiscal_year,filed,id)",
                "CREATE INDEX IF NOT EXISTS filings_date ON filings(filed,id)",
                "CREATE INDEX IF NOT EXISTS filings_form ON filings(ticker,form,filed)",
                "CREATE INDEX IF NOT EXISTS facts_scope ON facts(ticker,metric,fiscal_year,filing_id)",
                "CREATE INDEX IF NOT EXISTS facts_filing ON facts(filing_id)",
                "CREATE INDEX IF NOT EXISTS statement_scope ON statement_facts(ticker,statement,fiscal_year,filing_id)",
                "CREATE INDEX IF NOT EXISTS statement_filing ON statement_facts(filing_id)",
                "CREATE INDEX IF NOT EXISTS passages_filing ON passages(filing_id,id)",
                "CREATE INDEX IF NOT EXISTS external_updates_scope ON external_updates(ticker,source_type,published_at,id)",
            ):
                db.execute(sql)
            # Market disclosures use their local public calendar day, rather
            # than UTC midnight (CNINFO date stamps can fall on the prior UTC day).
            if db.execute("PRAGMA user_version").fetchone()[0] < 7:
                for row in db.execute("SELECT id,payload FROM external_updates").fetchall():
                    update = ExternalUpdate.model_validate_json(row["payload"])
                    db.execute("UPDATE external_updates SET published_at=? WHERE id=?",
                               (update.available_day.isoformat(), row["id"]))
            db.execute("PRAGMA user_version=7")

    def _ensure_writable(self) -> None:
        if self.readonly:
            raise RuntimeError("Readonly corpus cannot be modified")

    @contextmanager
    def connect(self):
        db = (sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=20)
              if self.readonly else sqlite3.connect(self.path, timeout=20))
        # Web requests, evaluators, and MCP calls can briefly overlap a schema
        # or evidence write.  Wait for that short critical section instead of
        # surfacing a transient "database is locked/readonly" error.
        db.execute("PRAGMA busy_timeout = 5000")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def _write_connect(self):
        """Serialise writers in-process, then take the usual connection.

        Readers never take this lock, so a parallel evaluator keeps reading while
        a refresh or a repaired filing writes.
        """
        self._ensure_writable()
        with self._write_lock, self.connect() as db:
            yield db

    def ingest(
        self,
        filing: Filing,
        facts: list[Fact],
        passages: list[Passage],
        statement_facts: list[StatementFact] | None = None,
    ) -> dict:
        statement_facts = statement_facts or []
        if any(x.filing_id != filing.id or x.ticker != filing.ticker for x in facts + passages):
            raise ValueError("Evidence must belong to its declared filing and company")
        if any(x.filing_id != filing.id or x.ticker != filing.ticker for x in statement_facts):
            raise ValueError("Statement evidence must belong to its declared filing")
        with self._write_connect() as db:
            # A refresh replaces this accession atomically, including its lexical index.
            prior = db.execute("SELECT id FROM passages WHERE filing_id=?", (filing.id,))
            for row in prior.fetchall():
                db.execute("DELETE FROM passages_fts WHERE id=?", (row["id"],))
            db.execute("DELETE FROM facts WHERE filing_id=?", (filing.id,))
            db.execute("DELETE FROM statement_facts WHERE filing_id=?", (filing.id,))
            db.execute("DELETE FROM passages WHERE filing_id=?", (filing.id,))
            db.execute(
                "INSERT OR REPLACE INTO filings (id,ticker,filed,payload,period_end,fiscal_year,form) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    filing.id,
                    filing.ticker,
                    filing.filed.isoformat(),
                    # Record which extractor produced this parse so a stale one
                    # is detectable and re-extracts on next use.
                    filing.model_copy(
                        update={"parser_version": PARSER_VERSION}
                    ).model_dump_json(),
                    filing.period_end.isoformat(),
                    filing.period_end.year,
                    filing.form,
                ),
            )
            db.executemany(
                "INSERT INTO facts VALUES (?,?,?,?,?,?)",
                [
                    (f.id, f.filing_id, f.ticker, f.metric, f.fiscal_year, f.model_dump_json())
                    for f in facts
                ],
            )
            db.executemany(
                "INSERT INTO statement_facts VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        f.id,
                        f.filing_id,
                        f.ticker,
                        f.statement,
                        f.metric,
                        f.fiscal_year,
                        f.model_dump_json(),
                    )
                    for f in statement_facts
                ],
            )
            db.executemany(
                "INSERT INTO passages VALUES (?,?,?,?)",
                [(p.id, p.filing_id, p.ticker, p.model_dump_json()) for p in passages],
            )
            db.executemany(
                "INSERT INTO passages_fts VALUES (?,?,?)",
                [(p.id, p.text, p.section) for p in passages],
            )
        return {
            "filing": filing.id,
            "facts": len(facts),
            "statement_facts": len(statement_facts),
            "passages": len(passages),
        }

    def register_company(self, ticker: str, company) -> None:
        from .domain import Company

        self._ensure_writable()
        validated = Company.model_validate(company)
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
            raise ValueError("Company registry keys must be uppercase ticker symbols")
        with self._write_connect() as db:
            prior = db.execute(
                "SELECT payload FROM discovered_companies WHERE ticker=?", (ticker,)
            ).fetchone()
            if prior and json.loads(prior["payload"])["cik"] != validated.cik:
                raise ValueError("A registered ticker cannot silently change company identity")
            db.execute(
                "INSERT OR REPLACE INTO discovered_companies VALUES (?,?)",
                (ticker, validated.model_dump_json()),
            )

    def add_facts(self, filing: Filing, facts: list[Fact]) -> int:
        """Add newly supported source facts without replacing existing accession evidence."""
        if any(f.filing_id != filing.id or f.ticker != filing.ticker for f in facts):
            raise ValueError("Facts must belong to the declared filing")
        with self._write_connect() as db:
            row = db.execute("SELECT payload FROM filings WHERE id=?", (filing.id,)).fetchone()
            if not row or not Filing.model_validate_json(row["payload"]).source_equals(filing):
                raise ValueError("Only an unchanged local filing can be enriched")
            added = 0
            for fact in facts:
                prior = db.execute("SELECT payload FROM facts WHERE id=?", (fact.id,)).fetchone()
                if prior:
                    if Fact.model_validate_json(prior["payload"]) != fact:
                        raise ValueError("Existing fact IDs cannot silently change")
                    continue
                db.execute(
                    "INSERT INTO facts VALUES (?,?,?,?,?,?)",
                    (
                        fact.id,
                        fact.filing_id,
                        fact.ticker,
                        fact.metric,
                        fact.fiscal_year,
                        fact.model_dump_json(),
                    ),
                )
                added += 1
        return added

    def add_statement_facts(self, filing: Filing, rows: list[StatementFact]) -> int:
        """Enrich an unchanged filing with wider statement or dimensional rows."""
        if any(row.filing_id != filing.id or row.ticker != filing.ticker for row in rows):
            raise ValueError("Statement evidence must belong to the declared filing")
        with self._write_connect() as db:
            prior = db.execute("SELECT payload FROM filings WHERE id=?", (filing.id,)).fetchone()
            if not prior or not Filing.model_validate_json(prior["payload"]).source_equals(filing):
                raise ValueError("Only an unchanged local filing can be enriched")
            added = 0
            for row in rows:
                existing = db.execute(
                    "SELECT payload FROM statement_facts WHERE id=?", (row.id,)
                ).fetchone()
                if existing:
                    if StatementFact.model_validate_json(existing["payload"]) != row:
                        raise ValueError("Existing statement IDs cannot silently change")
                    continue
                db.execute(
                    "INSERT INTO statement_facts VALUES (?,?,?,?,?,?,?)",
                    (
                        row.id,
                        row.filing_id,
                        row.ticker,
                        row.statement,
                        row.metric,
                        row.fiscal_year,
                        row.model_dump_json(),
                    ),
                )
                added += 1
        return added

    def ingest_external_update(self, update: ExternalUpdate) -> dict:
        """Store a transcript/news item outside official SEC filing tables."""
        validated = ExternalUpdate.model_validate(update)
        with self._write_connect() as db:
            prior = db.execute(
                "SELECT payload FROM external_updates WHERE id=?", (validated.id,)
            ).fetchone()
            if prior:
                original = ExternalUpdate.model_validate_json(prior["payload"])
                excluded = {"retrieved_at", "available_at"} if validated.publication_basis == "first_observed" else {"retrieved_at"}
                if original.model_dump(exclude=excluded) != validated.model_dump(exclude=excluded):
                    raise ValueError("Existing external update IDs cannot silently change")
                return {"id": validated.id, "stored": False}
            db.execute(
                "INSERT INTO external_updates (id,ticker,published_at,source_type,payload) VALUES (?,?,?,?,?)",
                (
                    validated.id,
                    validated.ticker,
                    validated.available_day.isoformat(),
                    validated.source_type,
                    validated.model_dump_json(),
                ),
            )
        return {"id": validated.id, "stored": True}

    def external_updates(
        self,
        as_of: date,
        ticker: str | None = None,
        source_type: str | None = None,
        limit: int = 10,
    ) -> list[ExternalUpdate]:
        if limit < 1 or limit > 100:
            raise ValueError("External update limit must be between 1 and 100")
        conditions = ["published_at<?"]
        arguments: list[object] = [(as_of + timedelta(days=1)).isoformat()]
        if ticker:
            conditions.append("ticker=?")
            arguments.append(ticker.upper())
        if source_type:
            if source_type not in ("transcript", "news", "hkex", "cninfo"):
                raise ValueError("Unknown external update type")
            conditions.append("source_type=?")
            arguments.append(source_type)
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM external_updates WHERE "
                + " AND ".join(conditions)
                + " ORDER BY published_at DESC,id DESC LIMIT ?",
                (*arguments, limit),
            ).fetchall()
        return [ExternalUpdate.model_validate_json(row[0]) for row in rows]

    def registered_companies(self) -> dict:
        with self.connect() as db:
            return {
                row["ticker"]: json.loads(row["payload"])
                for row in db.execute("SELECT * FROM discovered_companies")
            }

    def load_snapshot(self, path: Path) -> list[dict]:
        self._ensure_writable()
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        result = []
        for entry in snapshot["filings"]:
            result.append(
                self.ingest(
                    Filing.model_validate(entry["filing"]),
                    [Fact.model_validate(f) for f in entry["facts"]],
                    [Passage.model_validate(p) for p in entry["passages"]],
                    [StatementFact.model_validate(f) for f in entry.get("statement_facts", [])],
                )
            )
        return result

    def seed(self) -> None:
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM filings").fetchone()[0]:
                return
        self._ensure_writable()
        sample = files("filingcopilot").joinpath("samples", "sec-demo.json")
        if sample.is_file():
            self.load_snapshot(Path(str(sample)))

    def catalog(self, as_of: date, ticker: str | None = None) -> list[dict]:
        scope, arguments = self._scope(as_of, ticker)
        with self.connect() as db:
            rows = db.execute(
                "SELECT d.payload FROM filings d WHERE "
                + scope
                + " ORDER BY d.ticker, d.filed DESC, d.id DESC",
                arguments,
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def facts(
        self,
        ticker: str,
        metric: str,
        fiscal_year: int,
        as_of: date,
        forms: tuple[str, str] = ("10-K", "10-K/A"),
    ) -> list[Fact]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT f.payload FROM facts f JOIN filings d ON d.id=f.filing_id "
                "WHERE f.ticker=? AND f.metric=? AND f.fiscal_year=? AND d.filed<=? "
                "AND d.form IN (?,?) "
                "ORDER BY d.filed DESC, d.id DESC, f.id",
                (ticker, metric, fiscal_year, as_of.isoformat(), *forms),
            ).fetchall()
        return [Fact.model_validate_json(r[0]) for r in rows]

    def statement_facts(
        self,
        ticker: str,
        as_of: date,
        *,
        statement: str | None = None,
        fiscal_year: int | None = None,
        filing_id: str | None = None,
        period_end: date | None = None,
        limit: int = 5000,
    ) -> list[StatementFact]:
        """Read wider statement rows without treating absent rows as zero."""
        if limit < 1 or limit > 20000:
            raise ValueError("Statement row limit must be between 1 and 20000")
        conditions = ["f.ticker=?", "d.filed<=?"]
        arguments: list[object] = [ticker.upper(), as_of.isoformat()]
        if statement:
            if statement not in ("income", "balance", "cashflow", "other"):
                raise ValueError("Unknown statement kind")
            conditions.append("f.statement=?")
            arguments.append(statement)
        if fiscal_year is not None:
            conditions.append("f.fiscal_year=?")
            arguments.append(fiscal_year)
        if filing_id:
            conditions.append("f.filing_id=?")
            arguments.append(filing_id)
        if period_end:
            conditions.append("json_extract(f.payload,'$.end')=?")
            arguments.append(period_end.isoformat())
        with self.connect() as db:
            rows = db.execute(
                "SELECT f.payload FROM statement_facts f JOIN filings d ON d.id=f.filing_id "
                "WHERE " + " AND ".join(conditions) +
                " ORDER BY json_extract(f.payload,'$.end') DESC, f.id LIMIT ?",
                (*arguments, limit),
            ).fetchall()
        return [StatementFact.model_validate_json(row[0]) for row in rows]

    def passages(
        self,
        as_of: date,
        ticker: str | None = None,
        fiscal_year: int | None = None,
        filing_id: str | None = None,
    ) -> list[Passage]:
        scope, arguments = self._scope(as_of, ticker, fiscal_year)
        if filing_id:
            scope += " AND d.id=?"
            arguments.append(filing_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT p.payload FROM passages p JOIN filings d ON d.id=p.filing_id "
                "WHERE " + scope + " ORDER BY p.id",
                arguments,
            ).fetchall()
        return [Passage.model_validate_json(r[0]) for r in rows]

    def lexical(
        self,
        query: str,
        as_of: date,
        ticker: str | None,
        limit: int,
        fiscal_year: int | None = None,
        filing_id: str | None = None,
    ) -> list[tuple]:
        scope, arguments = self._scope(as_of, ticker, fiscal_year)
        if filing_id:
            scope += " AND d.id=?"
            arguments.append(filing_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT p.payload, bm25(passages_fts) score FROM passages_fts "
                "JOIN passages p ON p.id=passages_fts.id JOIN filings d ON d.id=p.filing_id "
                "WHERE passages_fts MATCH ? AND " + scope + " ORDER BY score, p.id LIMIT ?",
                (query, *arguments, limit),
            ).fetchall()
        return [(Passage.model_validate_json(r[0]), float(r[1])) for r in rows]

    @staticmethod
    def _scope(as_of: date, ticker: str | None = None, fiscal_year: int | None = None):
        conditions, arguments = ["d.filed<=?"], [as_of.isoformat()]
        if ticker is not None:
            conditions.append("d.ticker=?")
            arguments.append(ticker)
        if fiscal_year is not None:
            conditions.append("d.fiscal_year=?")
            arguments.append(fiscal_year)
        return " AND ".join(conditions), arguments

    def evidence(self, evidence_id: str, as_of: date) -> dict | None:
        with self.connect() as db:
            for table in ("facts", "statement_facts", "passages"):
                row = db.execute(
                    f"SELECT e.payload, d.payload FROM {table} e "
                    "JOIN filings d ON d.id=e.filing_id WHERE e.id=? AND d.filed<=?",
                    (evidence_id, as_of.isoformat()),
                ).fetchone()
                if row:
                    return {
                        **json.loads(row[0]),
                        "filing": json.loads(row[1]),
                        "kind": "fact"
                        if table == "facts"
                        else "statement_fact"
                        if table == "statement_facts"
                        else "passage",
                    }
            row = db.execute(
                "SELECT payload FROM external_updates WHERE id=? AND published_at<?",
                (evidence_id, (as_of + timedelta(days=1)).isoformat()),
            ).fetchone()
            if row:
                return {**json.loads(row["payload"]), "kind": "external_update"}
        return None

    def save_run(self, result: dict, package: dict | None = None) -> None:
        self._ensure_writable()
        with self._write_connect() as db:
            db.execute(
                "INSERT INTO runs (id,payload) VALUES (?,?)",
                (result["id"], json.dumps(result, ensure_ascii=False)),
            )
            if package is not None:
                db.execute(
                    "INSERT INTO run_packages (run_id,payload) VALUES (?,?)",
                    (result["id"], json.dumps(package, ensure_ascii=False)),
                )

    def update_run(self, result: dict, package: dict | None = None) -> None:
        """Replace a just-created run when lineage metadata is attached."""
        self._ensure_writable()
        with self._write_connect() as db:
            db.execute(
                "UPDATE runs SET payload=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), result["id"]),
            )
            if package is not None:
                db.execute(
                    "INSERT INTO run_packages (run_id,payload) VALUES (?,?) "
                    "ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload",
                    (result["id"], json.dumps(package, ensure_ascii=False)),
                )

    def run(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def run_package(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM run_packages WHERE run_id=?", (run_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def recent_runs(self, limit: int = 12) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM runs ORDER BY created DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]
