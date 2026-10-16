# Publication checks

The application is distributed as a local source release from this repository;
there is no hosted service and no PyPI package. This page documents how the
public source snapshot was prepared and what its checks cover.

## Selection and hygiene

- The public snapshot contains selected source, tests, docs, examples,
  dependency locks and key evidence summaries in a fresh root commit. Older
  development history, original worksheets and raw archives are retained
  locally and are not part of the published source.
- The public setup path uses `.env.example` only. The snapshot builder rejects
  absolute machine paths and excludes local-only helper and cache files before
  creating the selected copy.
- Tracked files were scanned with Gitleaks and configured-value checks. These
  statements cover the published snapshot; they are not a claim about any
  other checkout.
- Selected JSON summaries with local paths are publication derivatives, marked
  with `publication_redactions` and the original file SHA256. Results, timings,
  failure counts and human-label state are unchanged.

## Clean installation

A new Python 3.10 virtual environment without system site packages installed
`.[dev]` and wheel from the selected source; no private `.env` was present and
`pip check` reported no broken requirements. The final full regression in that
core-only environment recorded **1359 passing tests**; optional model inference
is not covered by that number. `requirements-lock.txt` is the retained Python
3.10 dependency snapshot, kept for provenance.

## Distribution checks

- README/docs links, wheel resources, the independent verifier, MCP and UI
  checks run locally; the hosted CI matrix (Windows/Ubuntu, Python 3.10/3.12,
  read-only repository permissions) runs the same suites on every push.
- The bundled [offline example](../examples/README.md) supports a key-free
  first run; its consistency audit does not authenticate remote source bytes.
- Docker Compose publishes the host port on `127.0.0.1` only; remote
  deployment and optional token authentication are outside the completed scope.
- The claim-period check runs on three curated display fixtures; the original
  archived packages can be replayed explicitly and remained unchanged.

## Scope statements

- MIT covers this project's code. Runtime dependencies and optional model
  weights retain their upstream licenses; model weights and private provider
  caches are excluded from publication. The bundled demo identifies SEC EDGAR
  HTML and official CompanyFacts as its sources and includes filing provenance.
- Human semantic support has a first recorded calibration from one non-domain
  reviewer; see [evaluation limits](evaluation.md) for its exact scope. ADS
  conversion inputs and full research coverage remain incomplete, and a
  recorded walkthrough or hosted demo has not been produced.
