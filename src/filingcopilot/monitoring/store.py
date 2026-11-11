"""SQLite persistence for Monitor v1.

The monitoring tables are additive and lazily created. They do not change the
existing corpus schema version, so old databases remain readable. Every write
goes through the corpus write lock; every derived row carries the version that
produced it.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from .models import Case, CaseReview, DisclosureEvent, WatchPolicy


def _now() -> float:
    return time.time()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MonitoringStore:
    def __init__(self, corpus):
        self.corpus = corpus
        self.ensure_schema()

    def ensure_schema(self) -> None:
        if getattr(self.corpus, "readonly", False):
            raise RuntimeError("Monitoring requires a writable local corpus")
        with self.corpus._write_connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitoring_policies (
                    ticker TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitoring_runs (
                    id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS monitoring_runs_started
                    ON monitoring_runs(started_at);
                CREATE TABLE IF NOT EXISTS filing_snapshots (
                    filing_id TEXT NOT NULL,
                    section TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (filing_id, section)
                );
                CREATE TABLE IF NOT EXISTS filing_diffs (
                    id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    filing_id TEXT NOT NULL,
                    prior_filing_id TEXT,
                    comparison_basis TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS filing_diffs_filing
                    ON filing_diffs(filing_id, created_at);
                CREATE TABLE IF NOT EXISTS disclosure_events (
                    id TEXT PRIMARY KEY,
                    event_key TEXT NOT NULL UNIQUE,
                    ticker TEXT NOT NULL,
                    filing_id TEXT NOT NULL,
                    prior_filing_id TEXT,
                    event_type TEXT NOT NULL,
                    subtype TEXT,
                    materiality TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS disclosure_events_filing
                    ON disclosure_events(filing_id, event_type);
                CREATE INDEX IF NOT EXISTS disclosure_events_ticker
                    ON disclosure_events(ticker, created_at);
                CREATE TABLE IF NOT EXISTS research_cases (
                    id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    filing_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    priority TEXT,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_cases_status
                    ON research_cases(status, created_at);
                CREATE TABLE IF NOT EXISTS case_reviews (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason_code TEXT,
                    comment TEXT,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS case_reviews_case
                    ON case_reviews(case_id, created_at);
                CREATE TABLE IF NOT EXISTS monitoring_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    actor TEXT,
                    action TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    before TEXT,
                    after TEXT
                );
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitoring_digests (
                    id TEXT PRIMARY KEY,
                    digest_date TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS monitoring_digests_date
                    ON monitoring_digests(digest_date,channel,created_at);
                CREATE TABLE IF NOT EXISTS monitoring_state (
                    ticker TEXT PRIMARY KEY,
                    last_accepted TEXT,
                    last_scan_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitoring_filings (
                    ticker TEXT NOT NULL,
                    filing_id TEXT NOT NULL,
                    accession TEXT,
                    form TEXT,
                    accepted TEXT,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at REAL NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (ticker, filing_id)
                );
                CREATE INDEX IF NOT EXISTS monitoring_filings_status
                    ON monitoring_filings(ticker,status,updated_at);
                CREATE TABLE IF NOT EXISTS monitoring_schema (
                    version INTEGER NOT NULL
                );
                """
            )
            db.execute("INSERT OR IGNORE INTO monitoring_schema(version) VALUES (1)")

    # Policies ---------------------------------------------------------------
    def get_policy(self, ticker: str) -> WatchPolicy | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT payload FROM monitoring_policies WHERE ticker=?",
                (ticker.upper(),),
            ).fetchone()
        return WatchPolicy.model_validate_json(row["payload"]) if row else None

    def list_policies(self) -> list[WatchPolicy]:
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT payload FROM monitoring_policies ORDER BY ticker"
            ).fetchall()
        return [WatchPolicy.model_validate_json(row["payload"]) for row in rows]

    def upsert_policy(self, policy: WatchPolicy) -> WatchPolicy:
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO monitoring_policies(ticker,payload,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(ticker) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (policy.ticker, policy.model_dump_json(), _now()),
            )
        return policy

    # Runs -------------------------------------------------------------------
    def start_run(self, payload: dict) -> str:
        run_id = "monitor-" + uuid.uuid4().hex
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO monitoring_runs(id,started_at,status,payload) VALUES (?,?,?,?)",
                (run_id, _now(), "running", json.dumps(payload, ensure_ascii=False)),
            )
        return run_id

    def finish_run(self, run_id: str, status: str, payload: dict) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                "UPDATE monitoring_runs SET finished_at=?, status=?, payload=? WHERE id=?",
                (_now(), status, json.dumps(payload, ensure_ascii=False), run_id),
            )

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT id,started_at,finished_at,status,payload FROM monitoring_runs "
                "ORDER BY started_at DESC LIMIT ?",
                (max(1, min(100, limit)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "status": row["status"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    # Ticker state -----------------------------------------------------------
    def get_state(self, ticker: str) -> dict | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT last_accepted,last_scan_at,payload FROM monitoring_state "
                "WHERE ticker=?",
                (ticker.upper(),),
            ).fetchone()
        if not row:
            return None
        return {
            "last_accepted": row["last_accepted"],
            "last_scan_at": row["last_scan_at"],
            "payload": json.loads(row["payload"]),
        }

    def set_state(self, ticker: str, last_accepted: str | None, payload: dict) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO monitoring_state(ticker,last_accepted,last_scan_at,payload) "
                "VALUES (?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                "last_accepted=excluded.last_accepted, last_scan_at=excluded.last_scan_at, "
                "payload=excluded.payload",
                (
                    ticker.upper(),
                    last_accepted,
                    _now(),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    # Per-accession processing state -----------------------------------------
    def get_filing(self, ticker: str, filing_id: str) -> dict | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT ticker,filing_id,accession,form,accepted,status,attempts,"
                "last_error,updated_at,payload FROM monitoring_filings "
                "WHERE ticker=? AND filing_id=?",
                (ticker.upper(), filing_id),
            ).fetchone()
        if not row:
            return None
        return {
            "ticker": row["ticker"],
            "filing_id": row["filing_id"],
            "accession": row["accession"],
            "form": row["form"],
            "accepted": row["accepted"],
            "status": row["status"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
            "updated_at": row["updated_at"],
            "payload": json.loads(row["payload"]),
        }

    def set_filing(
        self,
        ticker: str,
        filing_id: str,
        status: str,
        *,
        accession: str | None = None,
        form: str | None = None,
        accepted: str | None = None,
        error: str | None = None,
        payload: dict | None = None,
    ) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO monitoring_filings"
                "(ticker,filing_id,accession,form,accepted,status,attempts,last_error,updated_at,payload) "
                "VALUES (?,?,?,?,?,?,1,?,?,?) "
                "ON CONFLICT(ticker,filing_id) DO UPDATE SET "
                "accession=COALESCE(excluded.accession,accession), "
                "form=COALESCE(excluded.form,form), "
                "accepted=COALESCE(excluded.accepted,accepted), "
                "status=excluded.status, attempts=attempts+1, "
                "last_error=excluded.last_error, updated_at=excluded.updated_at, "
                "payload=excluded.payload",
                (
                    ticker.upper(),
                    filing_id,
                    accession,
                    form,
                    accepted,
                    status,
                    error,
                    _now(),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )

    def list_filings(self, ticker: str, status: str | None = None) -> list[dict]:
        query = (
            "SELECT ticker,filing_id,accession,form,accepted,status,attempts,"
            "last_error,updated_at,payload FROM monitoring_filings WHERE ticker=?"
        )
        args: list = [ticker.upper()]
        if status:
            query += " AND status=?"
            args.append(status)
        query += " ORDER BY updated_at DESC"
        with self.corpus.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [
            {
                "ticker": row["ticker"],
                "filing_id": row["filing_id"],
                "accession": row["accession"],
                "form": row["form"],
                "accepted": row["accepted"],
                "status": row["status"],
                "attempts": row["attempts"],
                "last_error": row["last_error"],
                "updated_at": row["updated_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    # Snapshots --------------------------------------------------------------
    def save_snapshot(
        self,
        filing_id: str,
        section: str,
        text: str,
        content_sha256: str,
        parser_version: str,
    ) -> None:
        payload = json.dumps({"section": section, "text": text}, ensure_ascii=False)
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO filing_snapshots"
                "(filing_id,section,content_sha256,parser_version,payload,created_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(filing_id,section) DO UPDATE SET "
                "content_sha256=excluded.content_sha256, parser_version=excluded.parser_version, "
                "payload=excluded.payload, created_at=excluded.created_at",
                (filing_id, section, content_sha256, parser_version, payload, _now()),
            )

    def get_snapshot(self, filing_id: str, section: str) -> dict | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT content_sha256,parser_version,payload FROM filing_snapshots "
                "WHERE filing_id=? AND section=?",
                (filing_id, section),
            ).fetchone()
        if not row:
            return None
        data = json.loads(row["payload"])
        data["content_sha256"] = row["content_sha256"]
        data["parser_version"] = row["parser_version"]
        return data

    # Diffs ------------------------------------------------------------------
    def save_diff(self, payload: dict) -> str:
        diff_id = str(payload.get("id") or "diff-" + uuid.uuid4().hex)
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO filing_diffs"
                "(id,ticker,filing_id,prior_filing_id,comparison_basis,extractor_version,payload,created_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "prior_filing_id=excluded.prior_filing_id, comparison_basis=excluded.comparison_basis, "
                "extractor_version=excluded.extractor_version, payload=excluded.payload, "
                "created_at=excluded.created_at",
                (
                    diff_id,
                    payload["ticker"],
                    payload["filing_id"],
                    payload.get("prior_filing_id"),
                    payload.get("comparison_basis") or "first_observation",
                    payload.get("extractor_version") or "monitor-v1",
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )
        return diff_id

    # Events -----------------------------------------------------------------
    def upsert_event(self, event: DisclosureEvent) -> DisclosureEvent:
        now = _now()
        with self.corpus._write_connect() as db:
            previous = db.execute(
                "SELECT payload FROM disclosure_events WHERE event_key=?",
                (event.event_key,),
            ).fetchone()
            if previous:
                old = DisclosureEvent.model_validate_json(previous["payload"])
                if old.status == "superseded":
                    event = event.model_copy(
                        update={
                            "status": "superseded",
                            "superseded_by_event_id": old.superseded_by_event_id,
                        }
                    )
            db.execute(
                "INSERT INTO disclosure_events"
                "(id,event_key,ticker,filing_id,prior_filing_id,event_type,subtype,materiality,status,payload,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(event_key) DO UPDATE SET "
                "materiality=excluded.materiality, status=excluded.status, "
                "payload=excluded.payload, updated_at=excluded.updated_at",
                (
                    event.id,
                    event.event_key,
                    event.ticker,
                    event.filing_id,
                    event.prior_filing_id,
                    event.event_type,
                    event.subtype,
                    event.materiality,
                    event.status,
                    event.model_dump_json(),
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT payload FROM disclosure_events WHERE event_key=?",
                (event.event_key,),
            ).fetchone()
        return DisclosureEvent.model_validate_json(row["payload"])

    def amending_event_ids(self, filing_id: str) -> list[str]:
        """Open event IDs from an amendment that points at this filing."""
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT id FROM disclosure_events "
                "WHERE status='open' AND json_extract(payload,'$.amends_filing_id')=?",
                (filing_id,),
            ).fetchall()
        return [row["id"] for row in rows]

    def supersede_events(
        self, filing_id: str, by_event_ids: list[str]
    ) -> list[str]:
        """Mark open events for an original filing superseded by an amendment."""
        if not by_event_ids:
            return []
        replacement = by_event_ids[0]
        superseded: list[str] = []
        with self.corpus._write_connect() as db:
            rows = db.execute(
                "SELECT payload FROM disclosure_events "
                "WHERE filing_id=? AND status='open'",
                (filing_id,),
            ).fetchall()
            for row in rows:
                event = DisclosureEvent.model_validate_json(row["payload"])
                event = event.model_copy(
                    update={
                        "status": "superseded",
                        "superseded_by_event_id": replacement,
                        "updated_at": _iso_now(),
                    }
                )
                db.execute(
                    "UPDATE disclosure_events SET status=?, payload=?, updated_at=? "
                    "WHERE id=?",
                    (event.status, event.model_dump_json(), _now(), event.id),
                )
                superseded.append(event.id)
        return superseded

    def list_events(
        self,
        *,
        ticker: str | None = None,
        filing_id: str | None = None,
        event_type: str | None = None,
        materiality: str | None = None,
        limit: int = 100,
    ) -> list[DisclosureEvent]:
        clauses: list[str] = []
        args: list = []
        if ticker:
            clauses.append("ticker=?")
            args.append(ticker.upper())
        if filing_id:
            clauses.append("filing_id=?")
            args.append(filing_id)
        if event_type:
            clauses.append("event_type=?")
            args.append(event_type)
        if materiality:
            clauses.append("materiality=?")
            args.append(materiality)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT payload FROM disclosure_events" + where +
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*args, max(1, min(500, limit))),
            ).fetchall()
        return [DisclosureEvent.model_validate_json(row["payload"]) for row in rows]

    def events_for_filing(self, filing_id: str) -> list[DisclosureEvent]:
        return self.list_events(filing_id=filing_id, limit=100)

    # Cases ------------------------------------------------------------------
    def create_case(self, case: Case) -> Case:
        now = _now()
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO research_cases"
                "(id,ticker,filing_id,status,priority,payload,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    case.id,
                    case.ticker,
                    case.filing_id,
                    case.status,
                    case.priority,
                    case.model_dump_json(),
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT payload FROM research_cases WHERE filing_id=?",
                (case.filing_id,),
            ).fetchone()
        return Case.model_validate_json(row["payload"])

    def get_case(self, case_id: str) -> Case | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT payload FROM research_cases WHERE id=?", (case_id,)
            ).fetchone()
        return Case.model_validate_json(row["payload"]) if row else None

    def get_case_by_filing(self, filing_id: str) -> Case | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT payload FROM research_cases WHERE filing_id=?", (filing_id,)
            ).fetchone()
        return Case.model_validate_json(row["payload"]) if row else None

    def update_case_if_revision(
        self,
        case: Case,
        expected_revision: int,
        *,
        review: CaseReview | None = None,
        audit_actor: str | None = None,
        audit_action: str | None = None,
        audit_before: str | None = None,
        audit_after: str | None = None,
    ) -> Case:
        """Atomically update a case only if its current state revision matches.

        A review and a regeneration both mutate status, so brief_version alone
        is insufficient: approving v1 does not change brief_version. The
        monotonic revision closes both write-order races.
        """
        with self.corpus._write_connect() as db:
            cursor = db.execute(
                "UPDATE research_cases SET status=?, priority=?, payload=?, updated_at=? "
                "WHERE id=? AND COALESCE(json_extract(payload,'$.revision'),1)=?",
                (
                    case.status,
                    case.priority,
                    case.model_dump_json(),
                    _now(),
                    case.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "案件已被其他操作更新；请刷新后重新审核。"
                )
            if review is not None:
                db.execute(
                    "INSERT INTO case_reviews"
                    "(id,case_id,action,actor,reason_code,comment,payload,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        review.id,
                        review.case_id,
                        review.action,
                        review.actor,
                        review.reason_code,
                        review.comment,
                        review.model_dump_json(),
                        _now(),
                    ),
                )
            if audit_action is not None:
                db.execute(
                    "INSERT INTO monitoring_audit"
                    "(created_at,actor,action,object_type,object_id,before,after) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        _now(),
                        audit_actor or "system",
                        audit_action,
                        "case",
                        case.id,
                        audit_before,
                        audit_after,
                    ),
                )
        return case

    def list_cases(
        self,
        *,
        status: str | None = None,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[Case]:
        clauses: list[str] = []
        args: list = []
        if status:
            clauses.append("status=?")
            args.append(status)
        if ticker:
            clauses.append("ticker=?")
            args.append(ticker.upper())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT payload FROM research_cases" + where +
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*args, max(1, min(500, limit))),
            ).fetchall()
        return [Case.model_validate_json(row["payload"]) for row in rows]

    # Reviews and audit ------------------------------------------------------
    def list_reviews(self, case_id: str) -> list[CaseReview]:
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT payload FROM case_reviews WHERE case_id=? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        return [CaseReview.model_validate_json(row["payload"]) for row in rows]

    def audit(
        self,
        *,
        actor: str,
        action: str,
        object_type: str,
        object_id: str,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO monitoring_audit"
                "(created_at,actor,action,object_type,object_id,before,after) "
                "VALUES (?,?,?,?,?,?,?)",
                (_now(), actor, action, object_type, object_id, before, after),
            )

    def last_digest(self, channel: str) -> dict | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT digest_date,created_at,payload FROM monitoring_digests "
                "WHERE channel=? AND status='sent' "
                "ORDER BY created_at DESC LIMIT 1",
                (channel,),
            ).fetchone()
        if not row:
            return None
        return {
            "digest_date": row["digest_date"],
            "created_at": row["created_at"],
            "payload": json.loads(row["payload"]),
        }

    def claim_digest(
        self, digest_date: str, channel: str, payload: dict, *, force: bool = False
    ) -> tuple[str | None, str]:
        """Atomically reserve one digest send and return its claim state."""
        digest_id = "digest-" + uuid.uuid4().hex
        with self.corpus._write_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,status FROM monitoring_digests "
                "WHERE digest_date=? AND channel=? "
                "ORDER BY created_at DESC LIMIT 1",
                (digest_date, channel),
            ).fetchone()
            if row and row["status"] in {"sent", "sending"} and not force:
                return None, (
                    "sent" if row["status"] == "sent" else "in_progress"
                )
            if row:
                digest_id = row["id"]
                db.execute(
                    "UPDATE monitoring_digests SET status='sending',payload=?,created_at=? "
                    "WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), _now(), digest_id),
                )
            else:
                db.execute(
                    "INSERT INTO monitoring_digests"
                    "(id,digest_date,channel,status,payload,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        digest_id,
                        digest_date,
                        channel,
                        "sending",
                        json.dumps(payload, ensure_ascii=False),
                        _now(),
                    ),
                )
        return digest_id, "claimed"

    def finish_digest(self, digest_id: str, status: str, payload: dict) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                "UPDATE monitoring_digests SET status=?,payload=?,created_at=? "
                "WHERE id=?",
                (status, json.dumps(payload, ensure_ascii=False), _now(), digest_id),
            )

    def add_delivery(
        self, case_id: str, channel: str, status: str, payload: dict
    ) -> str:
        delivery_id = "delivery-" + uuid.uuid4().hex
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT INTO notification_deliveries"
                "(id,case_id,channel,status,payload,created_at) VALUES (?,?,?,?,?,?)",
                (
                    delivery_id,
                    case_id,
                    channel,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )
        return delivery_id
