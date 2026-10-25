from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import closing, contextmanager
from datetime import date
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .corpus import Corpus
from .domain import DEFAULT_DOMAIN, Domain, load_domain, mentions
from .execution import RefreshBudgetExceeded, ResearchStopped, checkpoint, progress
from .local_models import verify_model_snapshot
from .retrieval_query import lexical_terms, retrieval_text
from .settings import Settings

ALIASES = DEFAULT_DOMAIN.query_expansions

# Finance questions are full of stable abbreviations.  Expand them before
# punctuation normalization so that ``P/E`` survives as one concept instead
# of becoming two unrelated tokens.  This is deliberately deterministic and
# recorded in the rewrite result; it does not add an opaque LLM call to search.
_ABBREVIATIONS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])P\s*/\s*E(?![A-Za-z0-9])", re.I), "price earnings ratio", "P/E"),
    (re.compile(r"(?<![A-Za-z0-9])PE(?![A-Za-z0-9])", re.I), "price earnings ratio", "PE"),
    (re.compile(r"(?<![A-Za-z0-9])EPS(?![A-Za-z0-9])", re.I), "earnings per share", "EPS"),
    (re.compile(r"(?<![A-Za-z0-9])YoY(?![A-Za-z0-9])", re.I), "year over year", "YoY"),
    (re.compile(r"(?<![A-Za-z0-9])QoQ(?![A-Za-z0-9])", re.I), "quarter over quarter", "QoQ"),
    (re.compile(r"(?<![A-Za-z0-9])FCF(?![A-Za-z0-9])", re.I), "free cash flow", "FCF"),
    (re.compile(r"(?<![A-Za-z0-9])CapEx(?![A-Za-z0-9])", re.I), "capital expenditures", "CapEx"),
    (re.compile(r"(?<![A-Za-z0-9])OCF(?![A-Za-z0-9])", re.I), "operating cash flow", "OCF"),
    (re.compile(r"(?<![A-Za-z0-9])CFO(?![A-Za-z0-9])", re.I), "operating cash flow", "CFO"),
    (re.compile(r"(?<![A-Za-z0-9])TTM(?![A-Za-z0-9])", re.I), "trailing twelve months", "TTM"),
)


def _canonicalize_abbreviations(value: str) -> tuple[str, list[str]]:
    """Return the canonical query and the abbreviations that were expanded."""
    found: list[str] = []
    text = value
    for pattern, replacement, label in _ABBREVIATIONS:
        text, count = pattern.subn(f" {replacement} ", text)
        if count:
            found.append(label)
    return text, found


def rewrite_query(
    query: str,
    aliases: dict | None = None,
    domain: Domain | None = None,
) -> dict:
    """Normalize and expand a user query without an LLM round trip.

    Domain overrides can add metric/ratio aliases without changing this
    module.  The normalized form is preserved for auditability, while the
    expanded form supplies English anchors for the English SEC corpus and
    keeps the original Chinese wording for local filings.
    """
    original = str(query)
    normalized = unicodedata.normalize("NFKC", original)
    normalized, canonical_terms = _canonicalize_abbreviations(normalized)
    normalized = re.sub(
        r"[\u3000\u3001\u2010\u2011\u2012\u2013\u2014\u2018\u2019\u201c\u201d，。；：！？（）【】《》,.;:!?()]+",
        " ",
        normalized,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    terms = ALIASES if aliases is None else aliases
    normalized_folded = normalized.casefold()
    additions: list[str] = [
        value for key, value in terms.items() if str(key).casefold() in normalized_folded
    ]
    if domain is not None:
        for key, metric in domain.metrics.items():
            candidates = [key.replace("_", " "), metric.label, *metric.aliases]
            if any(mentions(normalized, candidate) for candidate in candidates):
                additions.extend([key.replace("_", " "), metric.label, *metric.aliases])
        for key, ratio in domain.ratios.items():
            candidates = [key.replace("_", " "), ratio.label, *ratio.aliases]
            if any(mentions(normalized, candidate) for candidate in candidates):
                additions.extend([key.replace("_", " "), ratio.label, *ratio.aliases])
    unique = []
    seen = set()
    for value in additions:
        value = re.sub(r"\s+", " ", str(value)).strip()
        if value and value.casefold() not in seen and value.casefold() not in normalized.casefold():
            seen.add(value.casefold())
            unique.append(value)
    expanded = " ".join(part for part in [normalized, *unique] if part).strip()
    return {
        "original": original,
        "normalized": normalized,
        "expanded": expanded,
        "added_terms": unique,
        "canonical_terms": canonical_terms,
    }


def expand_query(query: str, aliases: dict | None = None) -> str:
    return rewrite_query(query, aliases=aliases)["expanded"]


_COVERAGE_SECTION_WEIGHT = 0.15


def _coverage_text(value: str) -> str:
    """Normalize text for deterministic term and phrase matching."""
    value = value.casefold().replace("-", " ").replace("_", " ").replace("/", " ")
    value = re.sub(r"[^a-z0-9%.\s\u4e00-\u9fff]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _term_forms(term: str) -> set[str]:
    """Small, explicit suffix stemmer; no language model, no hidden state."""
    forms = {term.casefold()}
    for suffix in ("ations", "ation", "ions", "ion", "ing", "ed", "es", "s"):
        if term.casefold().endswith(suffix) and len(term) - len(suffix) >= 4:
            forms.add(term.casefold()[: -len(suffix)])
    return forms


def _matches_coverage_term(term: str, text: str) -> bool:
    return any(form in text for form in _term_forms(term))


class Retriever:
    def __init__(self, corpus: Corpus, settings: Settings, model_path: Path | None = None):
        self.corpus = corpus
        self.settings = settings
        self.domain = load_domain(settings.domain_config, corpus.registered_companies())
        self.model = None
        self.model_path = model_path
        self.model_error = None
        self.lock = threading.Lock()
        # Derived vectors are reused across overlapping company/year scopes.
        # Keeping them per passage avoids rewriting a growing scope matrix for
        # every query that adds one new passage.
        self._embedding_memory: dict[str, dict[str, np.ndarray]] = {}
        # A bounded scope cache avoids repeatedly stacking the same passage
        # vectors for follow-up questions while keeping long-lived workers
        # from retaining every historical company/year combination forever.
        self._scope_memory: dict[str, np.ndarray] = {}
        self._scope_memory_limit = 8

    def _embedder(self):
        checkpoint()
        if self.model_error:
            return None
        if self.model is None:
            try:
                local = self.model_path or self.settings.home / "models" / "bge-local"
                if self.model_path and not local.is_dir():
                    raise FileNotFoundError("Explicit local embedding directory is missing")
                if self.model_path:
                    verify_model_snapshot(local, self.settings.embedding_model)
                    checkpoint()
                from fastembed import TextEmbedding

                self.model = TextEmbedding(
                    model_name=self.settings.embedding_model,
                    cache_dir=str(self.settings.home / "models"),
                    threads=self.settings.embedding_threads,
                    **(
                        {"specific_model_path": str(local)}
                        if self.model_path
                        or ((local / "model_optimized.onnx").exists()
                            and self.settings.embedding_model == "BAAI/bge-small-en-v1.5")
                        else {}
                    ),
                )
            except (ResearchStopped, RefreshBudgetExceeded):
                raise
            except Exception as exc:
                self.model_error = type(exc).__name__
        checkpoint()
        return self.model

    @contextmanager
    def _index_lock(self):
        # A waiter has its own deadline/cancellation, independent of the owner.
        while True:
            checkpoint()
            if self.lock.acquire(timeout=0.1):
                break
        try:
            checkpoint()
            yield
        finally:
            self.lock.release()

    @staticmethod
    def _encode_batches(model, passages):
        # Keep the exact texts/model/truncation. Nearby lengths reduce ONNX
        # padding work; vectors are associated with passage IDs before publish.
        ordered = sorted(passages, key=lambda passage: len(passage.text))
        # Bound preprocessing too: some providers tokenize the entire input
        # before their iterator yields even when batch_size is specified.
        for offset in range(0, len(ordered), 16):
            checkpoint()
            batch = ordered[offset : offset + 16]
            values = np.asarray(
                list(model.embed([p.text for p in batch], batch_size=16)), dtype=np.float32
            )
            checkpoint()
            if values.ndim != 2 or len(values) != len(batch):
                raise ValueError("embedding provider returned an unexpected row count or shape")
            progress("evidence", f"正在准备本地检索资料；已处理 {offset + len(batch)}/{len(passages)} 段。",
                     indexed_passages=offset + len(batch), total_passages=len(passages))
            yield batch, values

    def _encode_scope(self, model, passages):
        by_id = {passage.id: value for batch, values in self._encode_batches(model, passages)
                 for passage, value in zip(batch, values, strict=True)}
        return np.vstack([by_id[passage.id] for passage in passages])

    def _vectors(self, passages):
        model = self._embedder()
        if model is None:
            return None
        local = self.model_path or self.settings.home / "models" / "bge-local"
        manifest_path = local / "manifest.json"
        revision = (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if self.model_path and manifest_path.exists()
            else json.loads(manifest_path.read_text()).get("revision", "")
            if manifest_path.exists() and self.settings.embedding_model == "BAAI/bge-small-en-v1.5"
            else ""
        )
        if self.model_path and not manifest_path.exists():
            raise ValueError("Explicit local embedding directory requires a version manifest")
        identity = self.settings.embedding_model + ":" + version("fastembed") + ":" + revision
        passage_ids = [p.id for p in passages]
        vector_dir = self.settings.home / "vectors"
        vector_dir.mkdir(exist_ok=True)
        fingerprint = hashlib.sha256((identity + "|" + "|".join(passage_ids)).encode()).hexdigest()
        path = vector_dir / (fingerprint + ".npy")
        path.parent.mkdir(exist_ok=True)
        if fingerprint in self._scope_memory:
            return self._scope_memory[fingerprint]
        if path.exists():
            values = np.load(path, allow_pickle=False, mmap_mode="r")
            self._remember_scope(fingerprint, values)
            return values

        # New cache entries are stored per passage.  The legacy scope/index
        # files below remain readable for existing checkouts and are imported
        # into the persistent cache on first use.
        persistent = self._persistent_vectors(identity, passages, model, vector_dir)
        if persistent is not None:
            self._remember_scope(fingerprint, persistent)
            return persistent

        index_path = vector_dir / (hashlib.sha256(identity.encode()).hexdigest() + "-index.json")
        prior = {}
        if index_path.exists():
            try:
                prior = json.loads(index_path.read_text())
                prior_path = vector_dir / prior["file"]
                prior_ids = list(prior["ids"])
                universe = np.load(prior_path, allow_pickle=False, mmap_mode="r")
                ids = {passage_id: i for i, passage_id in enumerate(prior_ids)}
                if all(passage_id in ids for passage_id in passage_ids):
                    return universe[[ids[passage_id] for passage_id in passage_ids]]
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                prior = {}

        # Reuse a prior universe when a new fiscal-year/company scope overlaps it.
        # Without this branch every slightly different query re-embeds thousands of
        # passages, making hybrid retrieval appear unusably slow on a growing corpus.
        missing = [p for p in passages if p.id not in ids] if prior else list(passages)
        if prior and missing:
            new_values = self._encode_scope(model, missing)
            combined_ids = prior_ids + [p.id for p in missing]
            combined_values = np.concatenate((np.asarray(universe), new_values), axis=0)
            combined_fingerprint = hashlib.sha256(
                (identity + "|" + "|".join(combined_ids)).encode()
            ).hexdigest()
            combined_path = vector_dir / (combined_fingerprint + ".npy")
            self._write_vectors(combined_path, combined_values)
            self._write_index(index_path, {"file": combined_path.name, "ids": combined_ids})
            combined_lookup = {passage_id: i for i, passage_id in enumerate(combined_ids)}
            values = combined_values[[combined_lookup[passage_id] for passage_id in passage_ids]]
            self._remember_scope(fingerprint, values)
            return values

        values = self._encode_scope(model, missing)
        self._write_vectors(path, values)
        self._write_index(index_path, {"file": path.name, "ids": passage_ids})
        self._remember_scope(fingerprint, values)
        return values

    def _remember_scope(self, fingerprint: str, values) -> None:
        if fingerprint in self._scope_memory:
            return
        if len(self._scope_memory) >= self._scope_memory_limit:
            self._scope_memory.pop(next(iter(self._scope_memory)))
        self._scope_memory[fingerprint] = values

    def _persistent_vectors(self, identity, passages, model, vector_dir):
        """Read or append vectors without copying an entire prior scope."""
        db_path = vector_dir / "embeddings.sqlite3"
        passage_ids = [p.id for p in passages]
        memory = self._embedding_memory.setdefault(identity, {})
        try:
            with closing(sqlite3.connect(db_path, timeout=20)) as db, db:
                db.execute("PRAGMA busy_timeout=5000")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS embeddings ("
                    "identity TEXT NOT NULL, passage_id TEXT NOT NULL, "
                    "dim INTEGER NOT NULL, vector BLOB NOT NULL, "
                    "PRIMARY KEY(identity, passage_id))"
                )
                missing_ids = [passage_id for passage_id in passage_ids if passage_id not in memory]
                for start in range(0, len(missing_ids), 900):
                    chunk = missing_ids[start : start + 900]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = db.execute(
                        f"SELECT passage_id,dim,vector FROM embeddings "
                        f"WHERE identity=? AND passage_id IN ({placeholders})",
                        [identity, *chunk],
                    ).fetchall()
                    for passage_id, dim, raw in rows:
                        value = np.frombuffer(raw, dtype=np.float32).copy()
                        if value.size == int(dim):
                            memory[passage_id] = value

                # Import the old cache once when available, so upgrading does
                # not discard an expensive local embedding pass.
                still_missing = [p for p in passages if p.id not in memory]
                if still_missing and not memory:
                    self._import_legacy_vectors(db, identity, vector_dir)
                    for start in range(0, len(passage_ids), 900):
                        chunk = passage_ids[start : start + 900]
                        placeholders = ",".join("?" for _ in chunk)
                        rows = db.execute(
                            f"SELECT passage_id,dim,vector FROM embeddings "
                            f"WHERE identity=? AND passage_id IN ({placeholders})",
                            [identity, *chunk],
                        ).fetchall()
                        for passage_id, dim, raw in rows:
                            value = np.frombuffer(raw, dtype=np.float32).copy()
                            if value.size == int(dim):
                                memory[passage_id] = value

                missing = [p for p in passages if p.id not in memory]
                # Release any legacy-import write transaction before inference.
                db.commit()
                for batch, values in self._encode_batches(model, missing):
                    db.executemany(
                        "INSERT OR REPLACE INTO embeddings(identity,passage_id,dim,vector) "
                        "VALUES (?,?,?,?)",
                        [
                            (identity, passage.id, int(value.size), value.tobytes())
                            for passage, value in zip(batch, values, strict=True)
                        ],
                    )
                    db.commit()
                    memory.update(
                        {
                            passage.id: value
                            for passage, value in zip(batch, values, strict=True)
                        }
                    )

            return np.vstack([memory[passage_id] for passage_id in passage_ids])
        except (OSError, sqlite3.Error, ValueError, KeyError):
            # Embeddings are optional derived data; lexical retrieval remains
            # available when the cache cannot be read or written.
            return None

    @staticmethod
    def _import_legacy_vectors(db, identity, vector_dir) -> None:
        index_path = vector_dir / (hashlib.sha256(identity.encode()).hexdigest() + "-index.json")
        if not index_path.exists():
            return
        try:
            prior = json.loads(index_path.read_text())
            prior_path = vector_dir / prior["file"]
            ids = list(prior["ids"])
            universe = np.load(prior_path, allow_pickle=False, mmap_mode="r")
            if len(ids) != len(universe):
                return
            db.executemany(
                "INSERT OR IGNORE INTO embeddings(identity,passage_id,dim,vector) "
                "VALUES (?,?,?,?)",
                [
                    (
                        identity,
                        passage_id,
                        int(value.size),
                        np.asarray(value, dtype=np.float32).tobytes(),
                    )
                    for passage_id, value in zip(ids, universe, strict=True)
                ],
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return

    @staticmethod
    def _write_vectors(path, values) -> None:
        """Atomically publish an embedding matrix for another process to reuse."""
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        with temp.open("wb") as handle:
            np.save(handle, values, allow_pickle=False)
        temp.replace(path)

    @staticmethod
    def _write_index(path, payload) -> None:
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(payload), encoding="utf-8")
        temp.replace(path)

    def _coverage_hits(self, terms, passages, *, section_weight):
        """IDF-weighted term coverage plus a small section prior.

        This is a deterministic second stage over the already scoped passages.
        It intentionally does not use the gold set, expected IDs, or a model.
        """
        if not terms or not passages:
            return []
        texts = [
            (passage, _coverage_text(passage.text + " " + passage.section))
            for passage in passages
        ]
        total = len(texts)
        document_frequency = {
            term: sum(1 for _, text in texts if _matches_coverage_term(term, text))
            for term in terms
        }
        idf = {
            term: math.log((total + 1) / (document_frequency[term] + 1)) + 1
            for term in terms
        }
        scored = []
        for passage, text in texts:
            matched = [term for term in terms if _matches_coverage_term(term, text)]
            if not matched:
                continue
            coverage = sum(idf[term] for term in matched)
            section = passage.section.casefold()
            prior = 0.0
            if "risk factor" in section:
                prior += 1.0
            elif "financial statement" in section:
                prior += 0.25
            elif "management" in section and "analysis" in section:
                prior += 0.25
            score = coverage + section_weight * prior
            scored.append((score, coverage, prior, matched, passage))
        scored.sort(key=lambda item: (-item[0], item[4].id))
        return [
            {
                "passage": passage.model_dump(mode="json"),
                "score": round(score, 6),
                "legs": {
                    "coverage": {
                        "raw_score": round(coverage, 6),
                        "section_prior": round(prior, 3),
                        "matched_terms": matched,
                    }
                },
                "anchor_coverage": len(matched),
            }
            for score, coverage, prior, matched, passage in scored
        ]

    def search(
        self,
        query: str,
        as_of: date,
        ticker: str | None = None,
        k: int = 5,
        fiscal_year: int | None = None,
        filing_id: str | None = None,
        lexical_only: bool = False,
        section_title: str | None = None,
        lexical_weight: float = 1.0,
        dense_weight: float = 1.0,
        rrf_k: int = 60,
        rerank_mode: str | None = None,
        anchor_weight: float = 0.05,
        coverage_section_weight: float = _COVERAGE_SECTION_WEIGHT,
        query_mode: str | None = None,
        candidate_limit: int | None = None,
        diagnostics: bool = False,
    ) -> dict:
        checkpoint()
        rerank_mode = rerank_mode or self.settings.rerank_mode
        query_mode = query_mode or self.settings.retrieval_query_mode
        candidate_limit = candidate_limit if candidate_limit is not None else self.settings.retrieval_candidate_limit
        if lexical_weight < 0 or dense_weight < 0:
            raise ValueError("retrieval fusion weights must be non-negative")
        if rrf_k < 0:
            raise ValueError("retrieval RRF constant must be non-negative")
        if anchor_weight < 0:
            raise ValueError("retrieval anchor weight must be non-negative")
        if coverage_section_weight < 0:
            raise ValueError("retrieval coverage section weight must be non-negative")
        if rerank_mode not in {"rrf", "anchor", "coverage"}:
            raise ValueError("unknown retrieval rerank mode")
        if query_mode not in {"full", "focused"} or not 1 <= candidate_limit <= 100:
            raise ValueError("invalid retrieval query mode or candidate limit")
        rewrite = rewrite_query(query.strip(), self.domain.query_expansions, self.domain)
        query = rewrite["expanded"]
        if query_mode == "focused":
            query = retrieval_text(query, self.domain, ticker, fiscal_year)
        terms = lexical_terms(query)
        fts = " OR ".join('"' + t + '"' for t in terms)
        if section_title and fts:
            fts = "(" + fts + ') AND section : "' + section_title.replace('"', '""') + '"'
        lexical = self.corpus.lexical(fts, as_of, ticker, candidate_limit, fiscal_year, filing_id) if fts else []
        passages = self.corpus.passages(as_of, ticker, fiscal_year, filing_id)
        if section_title:
            passages = [p for p in passages if section_title.casefold() in p.section.casefold()]
        # unicode61 does not segment continuous CJK prose. A bounded fallback
        # uses the already scoped passages, retaining the same publication,
        # company, year, filing and section boundaries as the vector leg.
        cjk_terms = [term for term in terms if re.fullmatch(r"[\u4e00-\u9fff]{2,}", term)]
        if cjk_terms and len(lexical) < candidate_limit:
            existing = {p.id for p, _ in lexical}
            matches = [(p, sum(term in p.text for term in cjk_terms))
                       for p in passages if p.id not in existing]
            matches = sorted((pair for pair in matches if pair[1]), key=lambda pair: (-pair[1], pair[0].id))
            lexical.extend(matches[:candidate_limit - len(lexical)])
        dense = []
        dense_used = False
        backend = "fts5"
        if not lexical_only and self.settings.embedding_mode == "local" and passages:
            try:
                with self._index_lock():
                    vectors = self._vectors(passages)
                    if vectors is not None:
                        model = self._embedder()
                        q = np.asarray(list(model.query_embed(query))[0], dtype=np.float32)
                        checkpoint()
                if vectors is not None:
                    dense_used = True
                    denom = np.linalg.norm(vectors, axis=1) * np.linalg.norm(q)
                    similarity = vectors @ q / np.maximum(denom, 1e-12)
                    dense = [
                        (passages[int(i)], float(similarity[i]))
                        for i in np.argsort(-similarity, kind="stable")[:candidate_limit]
                        if similarity[i] >= 0.50
                    ]
                    backend = "fts5+bge+rrf"
                    checkpoint()
            except (ResearchStopped, RefreshBudgetExceeded):
                raise
            except Exception as exc:
                # A damaged optional index or ONNX failure must not discard FTS results.
                self.model_error = type(exc).__name__
        if rerank_mode == "coverage":
            # Coverage is a deterministic rerank over the bounded candidate
            # pool. It must not scan every passage in the filing scope and must
            # not discard the FTS/dense evidence already retrieved above.
            candidates = {}
            for passage, _ in lexical:
                candidates[passage.id] = passage
            for passage, _ in dense:
                candidates[passage.id] = passage
            candidate_passages = list(candidates.values())
            ranked = self._coverage_hits(
                terms, candidate_passages, section_weight=coverage_section_weight
            )
            return {
                "query": query.strip(),
                "query_rewrite": {
                    "normalized": rewrite["normalized"],
                    "added_terms": rewrite["added_terms"],
                    "canonical_terms": rewrite["canonical_terms"],
                    "retrieval_text": query,
                    "mode": query_mode,
                },
                "backend": "fts5+bge+coverage" if dense_used else "fts5+coverage",
                "embedding_unavailable": self.model_error,
                "hits": ranked[: max(1, min(8, k))],
                "eligible_passages": len(passages),
                **(
                    {
                        "retrieval_diagnostics": {
                            "candidate_ids": [item["passage"]["id"] for item in ranked],
                            "candidate_limit_per_leg": candidate_limit,
                            "coverage_candidates": len(candidate_passages),
                            "coverage_lexical_candidates": len(lexical),
                            "coverage_dense_candidates": len(dense),
                            "rerank_mode": "coverage",
                        }
                    }
                    if diagnostics
                    else {}
                ),
                **({"section_title": section_title} if section_title else {}),
            }
        combined = {}
        for leg, rows in (("lexical", lexical), ("dense", dense)):
            for rank, (passage, raw) in enumerate(rows, start=1):
                item = combined.setdefault(
                    passage.id,
                    {"passage": passage.model_dump(mode="json"), "score": 0.0, "legs": {}},
                )
                weight = lexical_weight if leg == "lexical" else dense_weight
                item["score"] += weight / (rrf_k + rank)
                item["legs"][leg] = {"rank": rank, "raw_score": raw}
        if rerank_mode == "anchor":
            # A small deterministic bonus favors candidates that actually
            # contain the user's expanded query terms.  It is intentionally
            # optional so a later cross-encoder can be evaluated at the same
            # seam without changing the default RRF contract.
            for item in combined.values():
                text = item["passage"].get("text", "").casefold()
                coverage = sum(
                    1
                    for term in terms
                    if len(term) >= 3 and term.casefold() in text
                )
                item["score"] += anchor_weight * coverage
                item["anchor_coverage"] = coverage
        ranked = sorted(combined.values(), key=lambda x: (-x["score"], x["passage"]["id"]))
        return {
            "query": query.strip(),
            "query_rewrite": {
                "normalized": rewrite["normalized"],
                "added_terms": rewrite["added_terms"],
                "canonical_terms": rewrite["canonical_terms"],
                "retrieval_text": query,
                "mode": query_mode,
            },
            "backend": backend,
            "embedding_unavailable": self.model_error,
            "hits": ranked[: max(1, min(8, k))],
            "eligible_passages": len(passages),
            **({"retrieval_diagnostics": {"candidate_ids": [item["passage"]["id"] for item in ranked], "candidate_limit_per_leg": candidate_limit}} if diagnostics else {}),
            **({"section_title": section_title} if section_title else {}),
        }
