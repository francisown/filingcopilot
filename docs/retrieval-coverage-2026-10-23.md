# Retrieval coverage rerank — 2026-10-23

**Status:** implemented as the default retrieval rerank on 2026-10-23. The 140-question answer-level evaluation was **not** re-executed; this report covers exact-passage retrieval only.

## Summary

The previous default was an anchor bonus over the top lexical/dense candidates. On the frozen 200-question retrieval gold set, the local lexical-only baseline reached **Recall@5 0.255 / MRR@5 0.1417** (English 0.360/0.1865, Chinese 0.150/0.0968). The recorded hybrid/anchor baseline remains **0.260/0.150333**; the local environment did not have `fastembed` installed, so the coverage comparison is against lexical-only.

The new default is a deterministic coverage rerank. It reaches:

| Metric | Baseline (lexical) | Coverage rerank | Delta |
|---|---:|---:|---:|
| Recall@5 | 0.255 | **0.430** | +0.175 |
| MRR@5 | 0.1417 | **0.2448** | +0.103 |
| English R@5 / MRR | 0.360 / 0.187 | 0.420 / 0.213 | +0.060 / +0.026 |
| Chinese R@5 / MRR | 0.150 / 0.097 | 0.440 / 0.277 | +0.290 / +0.180 |
| Candidate misses | 83/200 | 0/200 | — |
| p50 / p95 latency | ~35 ms / ~? | 82 ms / 164 ms | slower |

The candidate-miss number changes meaning: coverage scores every passage in the scoped corpus, so a target with at least one matching term is always in the scored pool. The ranking, not candidate recall, becomes the bottleneck.

## What changed

1. **Bilingual query expansion.** The domain glossary now maps missing Chinese credit/finance terms to English filing vocabulary: `客户 → customers`, `营业利润 → operating income`, `经营现金流 → operating cash flow`, `监管 → regulatory`, `需求 → demand`, `展望/预期 → outlook/expect/future`, `分部/地域 → segment/geographic`, `毛利率/营业利润率 → gross/operating margin`, `债务/流动性/义务 → debt/liquidity/obligations`, `年内累计 → year to date six months three months`.
2. **IDF-weighted coverage.** Every scoped passage is scored by the sum of inverse-document-frequency weights of the query terms it contains. Rare anchors such as `regulatory` or `liquidity` outweigh common words such as `business` or `revenue`.
3. **Section title in matching.** The section title participates in term matching, so a query for `risk` can match a passage under `Item 1A. Risk Factors` even when the body uses a synonym.
4. **Small section prior.** Risk Factors get `+0.15`; Financial Statements and MD&A get `+0.0375`. This reflects the gold distribution (about 58% of expected passages are under Risk Factors), but it is a weak prior, not a hard filter.
5. **Deterministic suffix stemming.** `recalibrated`, `recalibration`, and `recalibrate` share a stem; no model is involved.

The implementation is `Retriever._coverage_hits` in `src/filingcopilot/retrieval.py`. The default is `rerank_mode="coverage"` in `Settings` and `.env.example`. `anchor` and `rrf` remain available for comparison and for the embedding-specific tests.

## Per-section result

| Topic | Questions | Recall@5 | MRR@5 |
|---|---:|---:|---:|
| business | 20 | 0.65 | 0.393 |
| cashflow | 20 | 0.55 | 0.303 |
| debt | 20 | 0.60 | 0.303 |
| segments | 20 | 0.50 | 0.283 |
| risk | 20 | 0.50 | 0.254 |
| guidance | 20 | 0.40 | 0.277 |
| margin | 20 | 0.35 | 0.235 |
| quarter | 20 | 0.35 | 0.168 |
| competition | 20 | 0.25 | 0.118 |
| growth | 20 | 0.15 | 0.113 |

The weak topics are informative. `growth` questions ask for causes of revenue/operating-income change; many expected passages are specific MD&A paragraphs, while the query terms also match generic Risk Factors language. `competition` has only three anchor terms and a large number of boilerplate passages. The next retrieval work should target these two topics, not add another global reranker.

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_retrieval_coverage.py `
  --output artifacts/retrieval-coverage.json
```

The script uses the frozen gold set, the local corpus, no model, no network, and no embeddings. The gold SHA256 is recorded in the output. The default `--home` is `.filingcopilot`.

## Limitations

- **Development set, not held-out.** The glossary and section weights were chosen while inspecting this gold set. The 0.430 is a development number and is optimistic. A held-out set or an independent human relevance study is still required.
- **Exact passage identity.** A semantically relevant passage that is not the frozen expected ID is counted as a miss. This is stricter than human citation support.
- **Gold selection bias.** The gold set was authored from the corpus; it may over-represent passages with obvious anchor terms.
- **Lexical only.** The comparison did not use `fastembed`; dense retrieval may change the ranking and latency. The recorded hybrid/anchor baseline remains in `docs/evaluation.md`.
- **No answer-level re-run.** The 140-question model evaluation and its 81/18/1 result are historical. The new retrieval path may change answer delivery, latency, and token use; that has not been measured.
- **Single run.** Latency is a shared Windows host observation, not a portable guarantee.

## Why this is still worth shipping

The previous default missed 83 of 200 targets before ranking; the new path misses none before ranking and more than doubles Chinese exact-passage recall. The failure modes are now visible by topic, and the next step is a targeted fix for `growth` and `competition`, not another blind reranker. That is a better engineering position than a single 0.26 number with no diagnosis.
