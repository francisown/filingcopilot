"""Local observation preferences and policy-gated filing triggers.

The watchlist itself is only a saved list. A trigger scan is an explicit local
action: it reports recent filings for saved tickers, and it starts a bounded
research job only when the operator has enabled the trigger policy. There is no
always-on daemon and no implicit model spend.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, timedelta

from pydantic import Field, field_validator

from .market import symbol
from .models import ResearchRequest, StrictModel

LIMIT = 30
TRIGGER_FORMS = ("10-K", "10-Q", "8-K")
TRIGGER_LIST_LIMIT = 100
STALE_START_SECONDS = 300.0

_SKIP_STATUSES = {"queued", "starting"}
_LABELS = {"10-K": "年度报告", "10-Q": "季度报告", "8-K": "当前报告"}


class WatchlistSelection(StrictModel):
    ticker: str = Field(min_length=1, max_length=15)

    @field_validator("ticker")
    @classmethod
    def listing(cls, value):
        return symbol(value)


def _policy(settings) -> dict:
    raw_forms = getattr(settings, "watchlist_trigger_forms", TRIGGER_FORMS) or TRIGGER_FORMS
    forms = []
    for item in raw_forms:
        form = str(item).strip().upper()
        if form and form not in forms:
            forms.append(form)
    return {
        "enabled": bool(getattr(settings, "watchlist_triggers_enabled", False)),
        "forms": forms or list(TRIGGER_FORMS),
        "lookback_days": max(
            1, int(getattr(settings, "watchlist_trigger_lookback_days", 30))
        ),
        "max_per_scan": max(
            1, int(getattr(settings, "watchlist_trigger_max_per_scan", 3))
        ),
    }


def _request_key(ticker: str, filing_id: str) -> str:
    digest = hashlib.sha256(f"{ticker}|{filing_id}".encode()).hexdigest()[:32]
    return "watchlist-" + digest


def _question(item: dict, filing: dict) -> str:
    ticker = item["ticker"]
    name = item.get("name") or ticker
    form = str(filing.get("form") or "").upper()
    label = _LABELS.get(form, form)
    period = filing.get("period_end")
    period_note = f"，报告期至 {period}" if period else ""
    return (
        f"{name}（{ticker}）于 {filing.get('filed')} 提交的 {form} {label}{period_note}："
        "请依据该申报说明关键财务变化、经营进展和新增风险；申报未披露的项目请明确列入缺口。"
    )


class Watchlist:
    def __init__(self, corpus, lookup, capabilities, settings=None):
        self.corpus = corpus
        self.lookup = lookup
        self.capabilities = capabilities
        self.settings = settings

    def items(self) -> dict:
        with self.corpus.connect() as db:
            rows = db.execute("SELECT payload,added FROM watchlist ORDER BY added,rowid").fetchall()
        return {
            "limit": LIMIT,
            "items": [
                {
                    **json.loads(row["payload"]),
                    "added_at": row["added"],
                    "capabilities": self.capabilities(json.loads(row["payload"])["ticker"]),
                }
                for row in rows
            ],
        }

    def add(self, ticker: str) -> dict:
        ticker = symbol(ticker)
        with self.corpus.connect() as db:
            exists = db.execute("SELECT 1 FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        if exists:
            return self.items()
        if count >= LIMIT:
            raise ValueError(f"自选列表最多 {LIMIT} 只证券，请先移除不再观察的股票。")
        result = self.lookup(ticker, translate=False)
        matches = [item for item in result["candidates"] if item["ticker"] == ticker]
        if len(matches) != 1:
            raise ValueError("未能核对所选证券。请从股票搜索候选中选择明确的上市代码后重试。")
        candidate = matches[0]
        payload = {
            "ticker": ticker,
            "name": candidate.get("name") or ticker,
            "exchange": candidate.get("exchange"),
            "currency": candidate.get("currency"),
            "product": candidate.get("product"),
            "type": candidate.get("type", "EQUITY"),
        }
        with self.corpus.connect() as db:
            # Recheck inside the write transaction so separate web/CLI processes
            # cannot exceed the limit or replace an already saved identity.
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT 1 FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
            if not exists:
                if db.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] >= LIMIT:
                    raise ValueError(f"自选列表最多 {LIMIT} 只证券，请先移除不再观察的股票。")
                db.execute(
                    "INSERT INTO watchlist(ticker,payload) VALUES (?,?)",
                    (ticker, json.dumps(payload, ensure_ascii=False)),
                )
        return self.items()

    def remove(self, ticker: str) -> dict:
        ticker = symbol(ticker)
        with self.corpus.connect() as db:
            db.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
        return self.items()

    def policy(self) -> dict:
        return _policy(self.settings)

    def triggers(self) -> dict:
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT ticker,filing_id,form,filed,question,status,job_id,note,"
                "created_at,updated_at FROM watchlist_triggers "
                "ORDER BY updated_at DESC, ticker, filing_id LIMIT ?",
                (TRIGGER_LIST_LIMIT,),
            ).fetchall()
        return {"policy": self.policy(), "items": [dict(row) for row in rows]}

    def _trigger_states(self) -> dict:
        with self.corpus.connect() as db:
            rows = db.execute(
                "SELECT ticker,filing_id,status,updated_at FROM watchlist_triggers"
            ).fetchall()
        return {
            (row["ticker"], row["filing_id"]): (row["status"], row["updated_at"])
            for row in rows
        }

    def _claim(self, candidate: dict) -> bool:
        """Reserve one (ticker, filing) trigger before spending a job slot."""
        now = time.time()
        with self.corpus.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,updated_at FROM watchlist_triggers "
                "WHERE ticker=? AND filing_id=?",
                (candidate["ticker"], candidate["filing_id"]),
            ).fetchone()
            if row is not None:
                stale_start = (
                    row["status"] == "starting"
                    and now - float(row["updated_at"]) > STALE_START_SECONDS
                )
                if row["status"] in _SKIP_STATUSES and not stale_start:
                    return False
                db.execute(
                    "UPDATE watchlist_triggers SET form=?,filed=?,question=?,"
                    "status='starting',note=NULL,updated_at=? "
                    "WHERE ticker=? AND filing_id=?",
                    (
                        candidate["form"],
                        candidate["filed"],
                        candidate["question"],
                        now,
                        candidate["ticker"],
                        candidate["filing_id"],
                    ),
                )
                return True
            db.execute(
                "INSERT INTO watchlist_triggers"
                "(ticker,filing_id,form,filed,question,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'starting',?,?)",
                (
                    candidate["ticker"],
                    candidate["filing_id"],
                    candidate["form"],
                    candidate["filed"],
                    candidate["question"],
                    now,
                    now,
                ),
            )
        return True

    def _resolve(self, candidate: dict, status: str, *, job_id=None, note=None) -> None:
        with self.corpus.connect() as db:
            db.execute(
                "UPDATE watchlist_triggers SET status=?,job_id=?,note=?,updated_at=? "
                "WHERE ticker=? AND filing_id=?",
                (
                    status,
                    job_id,
                    note,
                    time.time(),
                    candidate["ticker"],
                    candidate["filing_id"],
                ),
            )

    def scan(self, jobs, *, as_of: date | None = None) -> dict:
        """Report recent watchlist filings; enqueue only under the enabled policy."""
        policy = self.policy()
        as_of = as_of or date.today()
        items = self.items()["items"]
        result = {
            "policy": policy,
            "as_of": as_of.isoformat(),
            "candidates": [],
            "started": [],
            "skipped": [],
        }
        if not items:
            return result
        watch = {item["ticker"]: item for item in items}
        since = (as_of - timedelta(days=policy["lookback_days"])).isoformat()
        states = self._trigger_states()
        for filing in self.corpus.catalog(as_of):
            ticker = filing.get("ticker")
            if ticker not in watch:
                continue
            form = str(filing.get("form") or "").upper()
            if form not in policy["forms"]:
                continue
            filed = str(filing.get("filed") or "")
            filing_id = str(filing.get("id") or "")
            if not filing_id or not filed or filed < since:
                continue
            state = states.get((ticker, filing_id))
            if state is not None and state[0] in _SKIP_STATUSES:
                stale_start = (
                    state[0] == "starting"
                    and time.time() - float(state[1]) > STALE_START_SECONDS
                )
                if not stale_start:
                    continue
            result["candidates"].append(
                {
                    "ticker": ticker,
                    "name": watch[ticker].get("name") or ticker,
                    "filing_id": filing_id,
                    "form": form,
                    "filed": filed,
                    "period_end": filing.get("period_end"),
                    "question": _question(watch[ticker], filing),
                    "request_key": _request_key(ticker, filing_id),
                }
            )
        if not policy["enabled"]:
            result["note"] = "触发策略未启用；本次只报告候选，不会创建研究任务。"
            return result
        for candidate in result["candidates"]:
            if len(result["started"]) >= policy["max_per_scan"]:
                break
            if not self._claim(candidate):
                continue
            request = ResearchRequest(
                question=candidate["question"],
                ticker=candidate["ticker"],
                as_of=as_of,
                mode="reference",
                company=candidate["name"][:120],
                language="zh",
            )
            try:
                view = jobs.start(request, candidate["request_key"])
            except RuntimeError:
                # The shared queue is full. Stop this scan rather than treating
                # every remaining candidate as a failure.
                self._resolve(candidate, "skipped", note="queue_full")
                result["skipped"].append({**candidate, "reason": "queue_full"})
                break
            except ValueError as exc:
                note = str(exc)[:300]
                self._resolve(candidate, "skipped", note=note)
                result["skipped"].append({**candidate, "reason": note})
                continue
            except Exception as exc:
                note = type(exc).__name__
                self._resolve(candidate, "failed", note=note)
                result["skipped"].append({**candidate, "reason": note})
                continue
            self._resolve(candidate, "queued", job_id=view["id"])
            result["started"].append({**candidate, "job_id": view["id"]})
        return result
