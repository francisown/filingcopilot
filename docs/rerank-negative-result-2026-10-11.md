# When an English cross-encoder degraded bilingual filing retrieval

FilingCopilot retained its existing retrieval path after a fixed-model,
fixed-corpus ablation. Reordering eight public results or the full lexical/dense
candidate union reduced exact-passage recall and MRR, and increased latency.
This is a negative result for this configuration and task, not evidence that
cross-encoders or bilingual retrieval generally fail.

## Frozen setup

- Dataset: `evaluation/retrieval_gold_v2.json`, 200 corpus-anchored questions.
  SHA-256: `4bd7cde11a840a7c7ec45105a80ad0c8c656506cafa1bc032ef09021e12e29dc`.
- Candidate producer: focused query rewriting, existing BGE small English
  embedding model, 75 candidates per lexical/dense leg, scoped by company,
  public availability date and fiscal year. Candidate counts were not increased
  for this experiment. The union is deduplicated before scoring.
- Cross-encoder: publisher repository
  [Xenova/ms-marco-MiniLM-L-6-v2](https://huggingface.co/Xenova/ms-marco-MiniLM-L-6-v2),
  fixed revision `a09144355adeed5f58c8ed011d209bf8ee5a1fec`.
  ONNX file SHA-256: `c623d0bcb99f4622beb413eaef00cfbe5db20df9f1dd982da4b4f26022881870`.
  All tokenizer/model hashes are retained in the raw artifact.
- Evidence payloads and target IDs were unchanged. An older database file hash
  changed only in SQLite header metadata; a subsequent read-only snapshot
  preserves file bytes. No expected IDs were supplied to retrieval or scoring.

## Results

| Path | Questions | R@5 | MRR@5 | Warm P95 | Errors |
|---|---:|---:|---:|---:|---:|
| Existing hybrid/anchor baseline | 200 | 0.240 | 0.132083 | 71.965 ms | 0 |
| Cross-encoder over eight returned hits | 200 | 0.195 | 0.099833 | 543.002 ms | 0 |
| Cross-encoder over full candidate union | 200 | 0.120 | 0.064917 | 7479.197 ms | 0 |

Cold scorer setup was 350.652 ms, measured separately from warm query latency.
Initial download failures from earlier experiments are retained and are not
treated as neural quality measurements. This completed ablation had no setup
or inference errors. Raw rows, candidate misses, pool sizes and source hashes
are retained in the development archive.

## Interpretation and limits

The English MS MARCO model was not trained for exact annual-report paragraph
identity or the project's Chinese financial queries. Domain and language
mismatch are plausible explanations, **not demonstrated causes**. A hit can be
semantically relevant while missing the frozen exact target; this metric does
not measure human sentence-level citation support. The gold is corpus-anchored
and may have selection bias. No held-out human relevance study was completed.

Query rewriting improved MRR with the candidate limit held at 25 in the earlier
controlled comparison; that improvement should not be attributed to changing
the candidate count. Expanding the pool in this rerank experiment did not fix
the observed degradation. Production therefore retains the current anchor
path. Enabling any future candidate still requires improvement in both recall
and MRR on unchanged gold and an acceptable measured P95.

## Reproduce without changing runtime defaults

Use the registered local model snapshots and the existing evidence corpus:

```powershell
python scripts/evaluate_full_pool_rerank.py --output artifacts/full-pool-replay.json
```

Check the script's `--help` for explicit source/model paths on another machine.
It creates an immutable database snapshot and retains all rows. It does not
change application defaults. Windows host caches were not fully controlled;
latencies are observations on this host, not portable guarantees. This article
is prepared in the repository; it has not been published externally.
