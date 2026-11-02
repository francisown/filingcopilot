"""Bounded research jobs with optional durable job/event records.

The in-process cache keeps active futures and recent terminal views. When the
service owns a writable corpus, the same records are mirrored into SQLite so a
completed job and its events survive a process restart and an idempotency key
cannot start the same research twice after a reload.

Phase 1a persists job and event state only. A worker that disappeared is
reported as ``interrupted`` with a recovery note; it is never silently resumed,
because resuming agent state is a later, explicit phase.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from .execution import ResearchControl

# A live worker refreshes its jobs from a daemon thread. A different process
# that has not refreshed for this long is treated as gone, so its rows cannot
# occupy the shared queue forever.
STALE_SECONDS = 120.0
HEARTBEAT_SECONDS = 30.0
# A deterministic supervisor owns the terminal transition when a worker is
# blocked past its deadline. Cooperative checkpoints normally raise first; the
# grace period gives them time to finalize before the watchdog intervenes.
WATCHDOG_SECONDS = 5.0
WATCHDOG_GRACE_SECONDS = 30.0
WATCHDOG_MESSAGE = (
    "研究时间预算已用完，任务由确定性看门狗终止；已取得的内容仍保存在结果中。"
)
MAX_EVENTS_IN_MEMORY = 100
MAX_EVENTS_IN_STORE = 500
INTERRUPTED_MESSAGE = (
    "研究服务已重启，任务在中断前可能已保存部分结果；请在研究记录中查看，必要时重新发起。"
)


class ResearchJobConflict(ValueError):
    """A retained request key or server session cannot safely start this request."""


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


class CorpusJobStore:
    """SQLite-backed job records, scoped to one writable corpus."""

    def __init__(self, corpus):
        self.corpus = corpus

    @staticmethod
    def _job(row) -> dict:
        return {
            "id": row["id"],
            "request_key": row["request_key"],
            "owner": row["owner"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "elapsed_ms": row["elapsed_ms"],
            "heartbeat_at": row["heartbeat_at"],
            "request": json.loads(row["request"]),
            "result": json.loads(row["result"]) if row["result"] else None,
            "preview": json.loads(row["preview"]) if row["preview"] else None,
            "error": json.loads(row["error"]) if row["error"] else None,
            "events": [],
            "event_sequence": row["event_sequence"],
        }

    def find_key(self, key: str | None) -> dict | None:
        if not key:
            return None
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT * FROM research_jobs WHERE request_key=?", (key,)
            ).fetchone()
        return self._job(row) if row else None

    def load(self, job_id: str) -> dict | None:
        with self.corpus.connect() as db:
            row = db.execute(
                "SELECT * FROM research_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            events = db.execute(
                "SELECT payload FROM research_events WHERE job_id=? "
                "ORDER BY sequence DESC LIMIT ?",
                (job_id, MAX_EVENTS_IN_MEMORY),
            ).fetchall()
        job = self._job(row)
        job["events"] = [json.loads(item["payload"]) for item in reversed(events)]
        return job

    def save(self, job: dict) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                """
                INSERT INTO research_jobs
                    (id,request_key,owner,status,started_at,finished_at,elapsed_ms,
                     heartbeat_at,request,result,preview,error,event_sequence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    request_key=excluded.request_key,
                    owner=excluded.owner,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    elapsed_ms=excluded.elapsed_ms,
                    heartbeat_at=excluded.heartbeat_at,
                    request=excluded.request,
                    result=excluded.result,
                    preview=excluded.preview,
                    error=excluded.error,
                    event_sequence=excluded.event_sequence
                """,
                (
                    job["id"],
                    job.get("request_key"),
                    job["owner"],
                    job["status"],
                    job["started_at"],
                    job.get("finished_at"),
                    job.get("elapsed_ms"),
                    job.get("heartbeat_at", job["started_at"]),
                    _json(job["request"]),
                    _json(job.get("result")),
                    _json(job.get("preview")),
                    _json(job.get("error")),
                    int(job.get("event_sequence") or 0),
                ),
            )

    def save_event(self, job: dict, event: dict) -> None:
        sequence = int(event["sequence"])
        with self.corpus._write_connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO research_events"
                "(job_id,sequence,created_at,payload) VALUES (?,?,?,?)",
                (job["id"], sequence, time.time(), _json(event)),
            )
            db.execute(
                "UPDATE research_jobs SET heartbeat_at=?, event_sequence=? WHERE id=?",
                (time.time(), sequence, job["id"]),
            )
            if sequence > MAX_EVENTS_IN_STORE:
                db.execute(
                    "DELETE FROM research_events WHERE job_id=? AND sequence<=?",
                    (job["id"], sequence - MAX_EVENTS_IN_STORE),
                )

    def heartbeat(self, job_id: str, now: float | None = None) -> None:
        with self.corpus._write_connect() as db:
            db.execute(
                "UPDATE research_jobs SET heartbeat_at=? WHERE id=?",
                (time.time() if now is None else now, job_id),
            )

    def active_count(self, stale_before: float) -> int:
        terminal = tuple(sorted(_TERMINAL))
        placeholders = ",".join("?" for _ in terminal)
        with self.corpus.connect() as db:
            return int(
                db.execute(
                    f"SELECT COUNT(*) FROM research_jobs "
                    f"WHERE status NOT IN ({placeholders}) AND heartbeat_at>=?",
                    (*terminal, stale_before),
                ).fetchone()[0]
            )

    def expire_stale(self, owner: str, stale_before: float) -> int:
        """Mark rows from a dead process without touching a live sibling."""
        terminal = tuple(sorted(_TERMINAL))
        placeholders = ",".join("?" for _ in terminal)
        now = time.time()
        error = _json({"type": "ProcessRestart", "message": INTERRUPTED_MESSAGE})
        with self.corpus._write_connect() as db:
            cursor = db.execute(
                f"UPDATE research_jobs SET status='interrupted', "
                f"finished_at=?, elapsed_ms=CAST((?-started_at)*1000 AS INTEGER), "
                f"error=? WHERE owner<>? AND status NOT IN ({placeholders}) "
                f"AND heartbeat_at<?",
                (now, now, error, owner, *terminal, stale_before),
            )
            return int(cursor.rowcount or 0)

    def mark_interrupted(self, job_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        error = _json({"type": "ProcessRestart", "message": INTERRUPTED_MESSAGE})
        terminal = tuple(sorted(_TERMINAL))
        placeholders = ",".join("?" for _ in terminal)
        with self.corpus._write_connect() as db:
            db.execute(
                f"UPDATE research_jobs SET status='interrupted', finished_at=?, "
                f"elapsed_ms=CAST((?-started_at)*1000 AS INTEGER), error=? "
                f"WHERE id=? AND status NOT IN ({placeholders})",
                (now, now, error, job_id, *terminal),
            )

    def prune(self, total_limit: int) -> None:
        """Keep every live row plus the newest terminal rows, bounded overall."""
        terminal = tuple(sorted(_TERMINAL))
        placeholders = ",".join("?" for _ in terminal)
        with self.corpus._write_connect() as db:
            active = int(
                db.execute(
                    f"SELECT COUNT(*) FROM research_jobs "
                    f"WHERE status NOT IN ({placeholders})",
                    terminal,
                ).fetchone()[0]
            )
            keep = max(0, total_limit - active)
            rows = db.execute(
                f"SELECT id FROM research_jobs WHERE status IN ({placeholders}) "
                f"ORDER BY COALESCE(finished_at,started_at) DESC LIMIT -1 OFFSET ?",
                (*terminal, keep),
            ).fetchall()
            for row in rows:
                db.execute("DELETE FROM research_events WHERE job_id=?", (row["id"],))
                db.execute("DELETE FROM research_jobs WHERE id=?", (row["id"],))


class NullJobStore:
    """No-op store for tests and read-only services without a writable corpus."""

    def find_key(self, key):
        return None

    def load(self, job_id):
        return None

    def save(self, job):
        return None

    def save_event(self, job, event):
        return None

    def heartbeat(self, job_id, now=None):
        return None

    def active_count(self, stale_before):
        return None

    def expire_stale(self, owner, stale_before):
        return 0

    def mark_interrupted(self, job_id, now=None):
        return None

    def prune(self, total_limit):
        return None


_TERMINAL = {"completed", "cancelled", "timed_out", "failed", "interrupted"}


class ResearchJobs:
    TERMINAL = _TERMINAL

    def __init__(self, service, workers=2, capacity=4, history=64):
        self.service = service
        self.capacity = capacity
        self.history = history
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research")
        self.lock = threading.RLock()
        self.jobs = OrderedDict()
        self.session = uuid.uuid4().hex
        corpus = getattr(service, "corpus", None)
        writable = corpus is not None and not getattr(corpus, "readonly", False)
        self.store = CorpusJobStore(corpus) if writable else NullJobStore()
        # A live sibling process keeps its rows fresh from its own heartbeat
        # thread. Stale rows belong to a process that is gone.
        try:
            self.store.expire_stale(self.session, time.time() - STALE_SECONDS)
        except Exception:
            # Persistence is a recovery aid, not a reason to refuse research.
            # The next explicit access marks the individual row.
            pass
        self._stop = threading.Event()
        self._durable = writable
        # Start each daemon only when there is work to protect. A server that
        # never starts a job never opens a background SQLite connection, and an
        # idle loop exits after its last job so discarded app objects do not
        # keep polling a corpus they no longer serve.
        self._heartbeat_thread = None
        self._watchdog_thread = None
        self.watchdog_interval = WATCHDOG_SECONDS
        self.watchdog_grace = WATCHDOG_GRACE_SECONDS

    @staticmethod
    def validate_key(key):
        if key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", key):
            raise ValueError("研究请求标识格式无效。")

    def check_session(self, session):
        if session is not None and session != self.session:
            raise ResearchJobConflict(
                "研究服务已重启，无法继续确认之前的启动请求。请先查看研究记录，再重新研究。"
            )

    def _ensure_heartbeat(self):
        if not self._durable:
            return
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="job-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            with self.lock:
                active = [
                    job["id"]
                    for job in self.jobs.values()
                    if job["status"] not in _TERMINAL
                ]
                if not active:
                    # No job needs a lease. Exit instead of polling; start()
                    # starts a fresh loop for the next job.
                    self._heartbeat_thread = None
                    return
            for job_id in active:
                try:
                    self.store.heartbeat(job_id)
                except Exception:
                    pass
            # Rows owned by a process that died stay "running" otherwise. Clean
            # them up here as well so a long-lived server presents an accurate
            # queue and history instead of waiting for each row to be read.
            try:
                self.store.expire_stale(self.session, time.time() - STALE_SECONDS)
            except Exception:
                pass

    def _ensure_watchdog(self):
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="job-watchdog", daemon=True
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self):
        while not self._stop.wait(self.watchdog_interval):
            with self.lock:
                now = time.monotonic()
                expired = []
                active = False
                for job_id, job in self.jobs.items():
                    if job["status"] in _TERMINAL:
                        continue
                    active = True
                    control = job.get("control")
                    deadline = control.deadline if control is not None else None
                    if (
                        deadline is not None
                        and not control.cancelled
                        and now - deadline >= self.watchdog_grace
                    ):
                        expired.append(job_id)
                for job_id in expired:
                    self._expire_job_locked(job_id)
                if not active:
                    # Nothing left to supervise. start() starts a new loop for
                    # the next job, so an idle server holds no daemon.
                    self._watchdog_thread = None
                    return

    def _expire_job_locked(self, job_id):
        job = self.jobs.get(job_id)
        if job is None or job["status"] in _TERMINAL:
            return
        job["status"] = "timed_out"
        job["watchdog"] = True
        job["finished_at"] = time.time()
        job["elapsed_ms"] = self._elapsed(job)
        job["error"] = {"type": "ResearchWatchdog", "message": WATCHDOG_MESSAGE}
        self._persist(job)
        self._publish(
            job_id,
            {
                "stage": "watchdog_timeout",
                "message": WATCHDOG_MESSAGE,
            },
        )

    @staticmethod
    def _stale(job: dict, now: float | None = None) -> bool:
        heartbeat = job.get("heartbeat_at") or job.get("started_at") or 0
        return (time.time() if now is None else now) - heartbeat > STALE_SECONDS

    def _revive_or_interrupt(self, job: dict) -> dict:
        if job["status"] not in _TERMINAL and self._stale(job):
            now = time.time()
            try:
                self.store.mark_interrupted(job["id"], now)
            except Exception:
                pass
            job["status"] = "interrupted"
            job["finished_at"] = now
            job["elapsed_ms"] = round((now - job["started_at"]) * 1000)
            job["error"] = {"type": "ProcessRestart", "message": INTERRUPTED_MESSAGE}
        return job

    def _find_job(self, key):
        if not key:
            return None
        job = next((j for j in self.jobs.values() if j["request_key"] == key), None)
        if job is not None:
            return job
        try:
            job = self.store.find_key(key)
        except Exception:
            return None
        return self._revive_or_interrupt(job) if job else None

    def start(self, request, request_key=None, session=None):
        self.validate_key(request_key)
        self.check_session(session)
        payload = request.model_dump(mode="json")
        # Check the key before planning or queue limits: reconnecting must not start work.
        with self.lock:
            existing = self._find_job(request_key)
            if existing:
                if existing["request"] != payload:
                    raise ResearchJobConflict("同一研究请求标识不能用于不同的问题。")
                return self.get(existing["id"])
        # Validate before queuing; no HTTP or model calls are needed for this plan.
        plan = self.service.research_plan(request)
        if not plan["ready"]:
            raise ValueError(plan["message"])
        with self.lock:
            # Concurrent starts may have finished planning with the same key.
            existing = self._find_job(request_key)
            if existing:
                if existing["request"] != payload:
                    raise ResearchJobConflict("同一研究请求标识不能用于不同的问题。")
                return self.get(existing["id"])
            persisted_active = self.store.active_count(time.time() - STALE_SECONDS)
            active = (
                persisted_active
                if persisted_active is not None
                else sum(j["status"] not in _TERMINAL for j in self.jobs.values())
            )
            if active >= self.capacity:
                raise RuntimeError("当前研究队列已满，请等待一项完成或取消后重试。")
            self._prune_cache_locked()
            job_id = "job-" + uuid.uuid4().hex
            now = time.time()
            job = {
                "id": job_id,
                "status": "queued",
                "started": time.monotonic(),
                "started_at": now,
                "finished_at": None,
                "elapsed_ms": None,
                "heartbeat_at": now,
                "event_sequence": 0,
                "owner": self.session,
                "events": [],
                "result": None,
                "preview": None,
                "error": None,
                "request_key": request_key,
                "request": payload,
            }
            self.jobs[job_id] = job
            try:
                self.store.save(job)
            except sqlite3.IntegrityError:
                # Another process inserted the same request key between our
                # lookup and this insert. Resolve to its row instead of failing
                # an otherwise valid reconnect or spending a second run.
                del self.jobs[job_id]
                existing = self._find_job(request_key) if request_key else None
                if existing is None:
                    raise
                if existing["request"] != payload:
                    raise ResearchJobConflict("同一研究请求标识不能用于不同的问题。") from None
                return self.get(existing["id"])
            except Exception:
                del self.jobs[job_id]
                raise
            self._prune_store_locked()
            self._ensure_heartbeat()
            self._ensure_watchdog()
            settings = getattr(self.service, "settings", None)
            job["control"] = ResearchControl(
                lambda event: self._publish(job_id, event),
                max_cost_usd=getattr(settings, "max_cost_usd", None),
                input_price_per_million=getattr(settings, "model_input_price_per_million", None),
                output_price_per_million=getattr(settings, "model_output_price_per_million", None),
            )
            self._publish(job_id, {"stage": "queued", "message": "研究已排队，等待执行。"})
            job["future"] = self.pool.submit(self._run, job_id, request)
            return self.get(job_id)

    def _prune_cache_locked(self):
        while len(self.jobs) >= self.history + self.capacity:
            old = next(
                (k for k, j in self.jobs.items() if j["status"] in _TERMINAL), None
            )
            if old is None:
                break
            del self.jobs[old]

    def _prune_store_locked(self):
        try:
            self.store.prune(self.history + self.capacity)
        except Exception:
            pass

    def recover(self, request_key, session=None):
        self.validate_key(request_key)
        self.check_session(session)
        with self.lock:
            job = self._find_job(request_key)
            if job is None:
                raise ValueError(
                    "未找到这次研究的启动记录，可能尚未提交或进度记录已过期。"
                    "请先查看研究记录；重新连接只查找已有任务，不会再次提交。"
                )
            return self.get(job["id"])

    def _publish(self, job_id, event):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            event = dict(event)
            if "preview" in event:
                job["preview"] = deepcopy(event.pop("preview"))
            sequence = job["events"][-1]["sequence"] + 1 if job["events"] else 1
            sequence = max(sequence, int(job.get("event_sequence") or 0) + 1)
            job["event_sequence"] = sequence
            job["heartbeat_at"] = time.time()
            job["events"].append(
                {**event, "sequence": sequence, "elapsed_ms": self._elapsed(job)}
            )
            job["events"] = job["events"][-MAX_EVENTS_IN_MEMORY:]
            try:
                self.store.save_event(job, job["events"][-1])
                if job.get("preview") is not None:
                    self.store.save(job)
            except Exception:
                pass

    @staticmethod
    def _elapsed(job):
        if job.get("elapsed_ms") is not None:
            return int(job["elapsed_ms"])
        if job.get("finished_at") and job.get("started_at"):
            return round((job["finished_at"] - job["started_at"]) * 1000)
        if job.get("started") is not None:
            return round(((job["finished_at"] or time.monotonic()) - job["started"]) * 1000)
        return round((time.time() - (job.get("started_at") or time.time())) * 1000)

    def _view(self, job):
        control = job.get("control")
        status = job["status"]
        if control is not None and status not in _TERMINAL and control.expired:
            status = "timing_out"
        usage = dict(control.usage) if control is not None else {}
        if not usage and isinstance(job.get("result"), dict):
            # A recovered job has no live control. The committed answer still
            # carries the same usage record, so reconnect clients see the cost
            # and token totals they saw before the restart.
            stored = job["result"].get("usage")
            if isinstance(stored, dict):
                usage = dict(stored)
        return {
            "id": job["id"],
            "status": status,
            "elapsed_ms": self._elapsed(job),
            "events": list(job["events"]),
            "result": job["result"],
            "preview": deepcopy(job["preview"]),
            "usage": usage,
            "error": job["error"],
            "time_budget_seconds": control.time_budget if control is not None else None,
            "remaining_seconds": control.remaining
            if control is not None and job["status"] not in _TERMINAL
            else None,
        }

    def _run(self, job_id, request):
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "cancelling" if job["control"].cancelled else "running"
            self._persist(job)
        try:
            answer = self.service.research(request, control=job["control"])
            with self.lock:
                job["result"] = answer.model_dump(mode="json")
                if job.get("watchdog"):
                    # The watchdog already declared the terminal state. A late
                    # return is preserved as evidence, not as a new outcome.
                    job["status"] = "timed_out"
                else:
                    job["status"] = (
                        "cancelled"
                        if job["control"].cancelled
                        else "timed_out"
                        if job["control"].expired
                        else "completed"
                    )
        except Exception as exc:
            # Exception strings can contain provider requests, keys or headers.
            with self.lock:
                if not job.get("watchdog"):
                    job["status"] = "failed"
                    job["error"] = {
                        "type": type(exc).__name__,
                        "message": "研究未完成。已获取的资料仍保存在本地；请检查公司、知悉日期或连接后重试。",
                    }
        finally:
            with self.lock:
                job["finished_at"] = time.time()
                job["elapsed_ms"] = self._elapsed(job)
                self._persist(job)

    def _persist(self, job):
        try:
            self.store.save(job)
        except Exception:
            pass

    def _load(self, job_id):
        try:
            job = self.store.load(job_id)
        except Exception:
            job = None
        return self._revive_or_interrupt(job) if job else None

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                job = self._load(job_id)
            if job is None:
                raise ValueError(
                    "研究任务不存在或服务已重启。已完成结果可在研究记录中查看。"
                )
            return self._view(job)

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                persisted = self._load(job_id)
                if persisted is None:
                    raise ValueError(
                        "研究任务不存在或服务已重启。已完成结果可在研究记录中查看。"
                    )
                if persisted["status"] in _TERMINAL:
                    return self._view(persisted)
                raise ResearchJobConflict(
                    "该任务由另一个进程执行，无法在此进程取消；请等待完成或在原进程取消。"
                )
            if job["status"] in _TERMINAL or not job["control"].cancel():
                return self._view(job)
            if job["future"].cancel():
                job["status"] = "cancelled"
                job["finished_at"] = time.time()
                job["elapsed_ms"] = self._elapsed(job)
                self._persist(job)
                self._publish(job_id, {"stage": "cancelled", "message": "排队中的研究已取消。"})
            else:
                job["status"] = "cancelling"
                self._persist(job)
                self._publish(
                    job_id,
                    {
                        "stage": "cancelling",
                        "message": "已请求取消，等待当前操作返回；随后停止新增调用并保存已核验结果。",
                    },
                )
            return self._view(job)

    def close(self):
        self._stop.set()
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None:
            thread.join(timeout=1)
        watchdog = self._watchdog_thread
        self._watchdog_thread = None
        if watchdog is not None:
            watchdog.join(timeout=1)
        with self.lock:
            for job_id in list(self.jobs):
                self.cancel(job_id)
        self.pool.shutdown(wait=False, cancel_futures=True)
