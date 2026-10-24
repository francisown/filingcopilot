# Data and model licenses

This repository's **source code** is MIT-licensed. The licenses below apply to
data and model artifacts that are downloaded or referenced at evaluation time.

| Artifact | License | Redistribution / commercial note |
|---|---|---|
| FilingCopilot source | MIT | See `LICENSE`. |
| FinanceBench (`PatronusAI/financebench`) | CC-BY-NC-4.0 | Non-commercial. The 150-row open subset and filing PDFs are not committed. Do not repackage them as a commercial dataset. |
| FinDER (`Linq-AI-Research/FinDER`) | CC-BY-NC-4.0 | Non-commercial. Parquet data is gitignored. |
| DocFinQA (`kensho/DocFinQA`) | MIT | Check the upstream filing-text terms before redistribution. |
| FiQA (`BeIR/fiqa`, `BeIR/fiqa-qrels`) | CC-BY-SA-4.0 | Share-alike; do not relicense derived qrels. |
| SEC EDGAR filings | Public disclosure; EDGAR access terms apply | Do not imply SEC endorsement. Respect fair-access/rate limits and the issuer's document terms. |
| BGE reranker (`BAAI/bge-reranker-v2-m3`, ONNX community export) | Apache-2.0 model card | The 570MB ONNX file is gitignored; verify the checksum before use. |
| FlashRank MiniLM | Apache-2.0 model card | Local cache is gitignored. |
| Market data / provider snapshots | Provider terms | Not covered by this repository's MIT license; verify before redistribution. |

## Rules

- Do not commit datasets, PDFs, model weights, provider snapshots, or API keys.
- Keep evaluation outputs that contain benchmark rows out of public releases
  unless the upstream license permits it.
- A benchmark result does not grant a license to the underlying documents.
- Commercial use of this repository still requires a separate review of the
  non-commercial benchmark licenses and any market-data/provider contracts.
- Model outputs are not investment advice and are not a substitute for a
  licensed professional's review.

When in doubt, link to the upstream source instead of redistributing the data.
