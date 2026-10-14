# Claim-period renderer fixtures

Three small, curated display inputs preserve original claims and recursive
period metadata from AMZN/META archived outputs. They omit model traces,
full provider price histories and unrelated evidence fields. They are not
research packages and are never inputs to financial or semantic scoring.
`manifest.json` binds their bytes and records each full original package hash.
The original 143-file/62MB input set remains unchanged in the private archive.

Default CI uses these portable fixtures plus controlled date/period cases.
For the broader original-input renderer regression, explicitly run:

```powershell
node scripts/check_claim_periods_ui.cjs --legacy-packages "<archive-path>"
```

This checks rendering/mutation only, not source truth, model quality or
independent verification of the original packages.
