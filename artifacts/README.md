# Reproducibility evidence

Most result JSON, traces, screenshots and local validation receipts are
generated artifacts. They are gitignored and are not part of the public source
snapshot.

Three frozen records are tracked because scripts consume them and because
`CONTRIBUTING.md` requires failed and passing evidence to remain reviewable:

| Record | Consumer |
|---|---|
| `retrieval-frozen-diagnosis-2026-10-11.json` | `scripts/evaluate_full_pool_rerank.py` (frozen gold/source hashes and negative retrieval results) |
| `third-round-cold-PDD-2026-10-11.json` | `scripts/compare_frozen_agents.py`, `scripts/inspect_foreign_share_sources.py` |
| `third-round-cold-BABA-2026-10-11.json` | `scripts/compare_frozen_agents.py`, `scripts/inspect_foreign_share_sources.py` |

These records embed source URLs and content SHA256 values. Their original
`.filingcopilot/` replay corpora were local working data and were never tracked;
a script that needs those cache directories must be pointed at a restored local
archive. The retained summaries still preserve the measured values, the exact
source hashes and the failed candidates.

The public repository keeps methodology and current limitations in:

- [Evaluation limits](../docs/evaluation.md)
- [Reproducing the evaluation](../docs/reproducing-evaluation.md)
- [FinanceBench results](../docs/validation/financebench-support-2026-10-26.json)

Do not treat local artifacts as a current production accuracy claim.
