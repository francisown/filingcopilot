# Independent offline verifier

Build a local distribution from the same audited source used by FilingCopilot:

```powershell
.\.venv\Scripts\python.exe scripts/build_verifier_distribution.py
pip install dist/filingcopilot_verify-1.0.0-py3-none-any.whl
filingcopilot-verify research-package.json
filingcopilot-verify research-package.json --corpus-home .filingcopilot --source-cache .filingcopilot/sec-cache --market-cache .filingcopilot/market-cache --external-cache .filingcopilot/external-cache
filingcopilot-verify --schema --output research-package-schema-v1.json
filingcopilot-verify research-package.json --source-cache .filingcopilot/sec-cache --badge consistency.svg --output audit.json
```

This is a separate wheel and CLI; installing the application, launching a
server, configuring model credentials or fetching model weights is unnecessary.
Runtime dependencies are BeautifulSoup, httpx, numpy, pydantic, python-dotenv
and tzdata. Audit calls do not perform network requests or load environment
configuration. The build uses local setuptools/wheel and never publishes.

Exit status 0 means the requested checks are consistent; 1 means invalid;
2 means an input could not be read. `--output` saves the JSON report. No report
certifies a narrative, investment decision, issuer signature or remote source.

Without a caller-trusted corpus/cache, only package consistency is checked.
The package's own checksum does not establish origin. With a corpus, matching
facts and source metadata are checked through a read-only SQLite connection.
With caches, formulas and bound original bytes can be replayed. Missing raw
sources remain `not_checked`; inspect these counts even when exit status is 0.
`--domain-config` and `--market-config` accept separate caller-trusted
registries. Never accept an untrusted package's registry as an independent
authority solely because the package supplied it.

The wheel embeds `build-manifest.json` with the source hashes of its copied
audit modules/resources, CLI and version source. Application/agent/API modules are excluded. The
supported public surfaces are the CLI and `audit.audit_package`; internal
modules are implementation details, not a generation SDK. The build uses pip's
isolated build environment from `pyproject.toml`; this is intentional because
Python 3.12 virtual environments do not promise an ambient `setuptools`
installation. Rebuild the wheel
when audit rules change; do not maintain a second hand-edited copy.

See the [versioned schema](research-package-schema-v1.json) and
[package protocol](research-package-protocol.md).
This wheel has not been uploaded to PyPI.

The optional SVG badge contains the exact package-file checksum and requested
mechanical checking scope. It does not authenticate an author, certify semantic
support or endorse investment judgments. This is a local artifact, not a public
badge service. This is an integration boundary, not an external certification.
