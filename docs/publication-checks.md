# Release checks

## Prepare the source

`scripts/build_public_snapshot.py` creates a source directory and ZIP from the
project's release file list. It excludes `.env`, runtime caches and local analysis
scripts, checks for configured secrets and machine paths, and writes a SHA256
manifest. The command requires new output paths:

```powershell
python scripts/build_public_snapshot.py --output .filingcopilot/release-source --zip dist/FilingCopilot-source.zip
```

The directory contains a separate Git root commit for packaging. Changes to the
existing repository should use its normal commit history.

## Validate

- Install the selected source in a fresh environment and run `pip check`.
- Run the checks in [CONTRIBUTING.md](../CONTRIBUTING.md) and the
  [CI workflow](../.github/workflows/ci.yml).
- Build the application and independent-verifier wheels; check their resources and
  the valid/tampered package examples.
- Check documentation links and compare a fresh clone with the release manifest.
- Scan the selected files for credentials, including image metadata.

The [2026-10-19 record](validation/live-evaluation-2026-10-19.json) covers the latest
live evaluation and its follow-up regression checks. The
[2026-10-18 checks](validation/reliability-followup-2026-10-18.json) cover the previous
application validation. The earlier
[clean-install record](validation/release-2026-10-18.json) applies to the source
revision described there. Local runs and hosted CI results are recorded separately.

## Files and licenses

The code uses the [MIT license](../LICENSE). Dependencies and optional model weights
retain their upstream licenses. Model weights and private provider caches are not
distributed. Bundled filing data identifies its SEC sources and filing metadata.

Selected historical JSON records may redact local paths; those records include
`publication_redactions` and the original SHA256. Evaluation values and label state
are preserved. See the [record index](../artifacts/README.md).
