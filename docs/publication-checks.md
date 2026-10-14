# Publication checks

This is source-release preparation. No GitHub push, PyPI upload or hosted
deployment has been performed. The application is distributed as a local source
release; this repository does not include a hosted service.

## Selected source release

This directory is a standalone source snapshot containing the selected source,
tests, documentation, examples and dependency lock. It does not include prior
Git history, private source caches, or the archived development artifacts.
Upload only the files in this snapshot; do not mirror a private development
checkout or its branches.

The private credential-import helper is ignored locally and explicitly excluded
by the snapshot builder. Public setup uses `.env.example`; it does not search
other projects for credentials. The builder rejects absolute machine paths
before creating the selected copy. Also inspect any extra files added to a ZIP.

8745 original artifact files, 917336368 bytes, were moved to the separate local
archive. Human worksheets and frozen contexts have ignored working copies;
their bytes and the configured credentials/gold were checked unchanged.
Old outputs and failures were not replaced by passing results. No evidence
attachments have been uploaded.

Selected JSON summaries with local paths are publication derivatives, marked
with `publication_redactions` and the original file SHA256. Results, timings,
failure counts and human-label state are unchanged. The displayed semantic
context has a relative home path; its original fingerprint is retained only
as provenance for the private original, not presented as a hash of the edited
context. Use the unmodified private manifest for frozen-run replay.

Machine-local cache paths are ignored and excluded from the publication. They
are not part of the application or its reproducible source inputs.

## Clean installation

A new Python 3.10 virtual environment, without system site packages, installed
`.[dev]` and wheel from the selected source. No private `.env` was present there,
and `pip check` reported no broken requirements. No model weights were installed.
First full run: 7 failures/1352 passes. Six mocked-vector tests incorrectly read
the optional fastembed package version; a seventh imported the optional SDK.
Only the tests' injected metadata/module boundaries were corrected. Production
retrieval, model choices and frozen results were unchanged. Final full regression: **1359 passed in 152.25 seconds** (153.547 seconds including
runner overhead). This was a new core-only venv; it does not certify optional
model inference or the hosted CI matrix.

`requirements-lock.txt` is the existing tested Python 3.10 dependency snapshot,
including optional packages; it is retained for provenance. The above fresh
core installation resolved pyproject bounds, rather than claiming a fresh test
of every optional package or pinned HuggingFace model. See
[test scope](test-profiles.md) and [evaluation limits](evaluation.md).

The subsequent publication path audit changed only public summaries, docs and
snapshot tooling/tests. Ten targeted tests passed, including an actual isolated
Git/ZIP build proving the retained private helper is omitted. Application source,
gold and protected samples are unchanged. The previously recorded 1359-test
fresh-clone run is historical validation of that application; the complete suite
was not rerun for this documentation/tooling follow-up.

## Distribution checks

Check the working tree and root-commit publication history with Gitleaks and
configured-value scans. Ensure `.env`, caches, old worksheets, Windows cache
folders and the full archived artifacts are absent from tracked files. Validate
the README/docs links, wheel resources, independent verifier, MCP and UI checks.
The provided [offline example](../examples/README.md) supports a key-free first run;
its consistency audit does not authenticate remote source bytes.

The release history is separate from the old development history. The previous
Gitleaks scan of the latter retained 24 detections for review, including hashes,
recovery identifiers and captured third-party text. That is not a zero-findings
or fully adjudicated history. Git author email metadata also exists. Publish
only the selected branch; never infer that all older history is safe to expose
from a clean release scan, and do not push all branches or mirror this checkout.

The existing GitHub Actions workflow covers Windows/Ubuntu and Python 3.10/3.12;
it requests read-only repository permissions. Local checks cannot establish that
these hosted jobs have passed: confirm the actual runs after publishing.
Docker Compose publishes the host port on `127.0.0.1`; remote deployment and
optional token authentication remain outside this local source release's completed scope.

## Third-party scope

MIT covers this project's code. Runtime dependencies and optional model weights
retain their upstream licenses; model weights and private provider caches are
excluded from publication. The bundled demo identifies SEC EDGAR HTML and
official CompanyFacts as its sources and includes filing provenance. Live
market access depends on the operator's provider access. No rights to redistribute
provider services, complete caches or arbitrary third-party transcripts are claimed.

Human semantic support is still null; ADS conversion inputs and full research
coverage remain incomplete. A real three-minute recording and publicly accessible
demo have not been produced. None is replaced by engineering pass counts.

Default frontend: 31 commands passed. The claim-period check now uses three
210KB display fixtures; its 142 original archived packages can also be replayed
explicitly and remained unchanged. Independent offline verification of 43 original
packages passed after archival.
