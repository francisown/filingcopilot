"""Local observation preferences, independent of frozen research and quote snapshots."""

import json

from pydantic import Field, field_validator

from .market import symbol
from .models import StrictModel

LIMIT = 30


class WatchlistSelection(StrictModel):
    ticker: str = Field(min_length=1, max_length=15)

    @field_validator("ticker")
    @classmethod
    def listing(cls, value):
        return symbol(value)


class Watchlist:
    def __init__(self, corpus, lookup, capabilities):
        self.corpus = corpus
        self.lookup = lookup
        self.capabilities = capabilities

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
