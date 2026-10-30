# Installation and local startup

Use Python 3.10 or later. The core install includes the web workspace, CLI,
filing tools and model-provider client. It does not require local embedding
models or GPU packages. A model API key is optional for a first trial with the
bundled financial data.

## Windows

Run `./setup.ps1` in PowerShell. It selects a compatible Python using the
Windows `py` launcher or `python`, then creates `.venv` and installs the core
dependencies. It reuses a compatible existing environment. An unsupported
existing environment is reported for manual repair rather than overwritten.

For a new environment, select an interpreter explicitly if needed:

```powershell
.\setup.ps1 -PythonExecutable '<path-to-python.exe>'
```

`-Language zh` selects Chinese setup and startup messages. The optional
`-Profile locked` installs the retained dependency snapshot, including
development and embedding dependencies. That snapshot is for Python 3.10;
newer Python versions should use the default core profile.

The setup script passes the official PyPI index when installing the project. This avoids
an incomplete global pip mirror causing the PEP 517 build environment to report that
`setuptools>=68` cannot be found.

Create `.env` from the example only if you do not already have it, then start:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item .env.example .env
}
$env:FC_EMBEDDING_MODE = 'off'
.\start.ps1
```

The fresh-install default is `http://127.0.0.1:8159/`. The launcher respects a
valid saved port or an explicit `-Port`; use `-Port 8159` to select the
documented port. It checks the loaded version before reusing an existing
service. `-CheckOnly -NoBrowser` inspects services without starting or
stopping them. Keep the service terminal running; press `Ctrl+C` there to stop.

## macOS and Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.'
if [ ! -f .env ]; then
    cp .env.example .env
fi
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve
```

The CLI also defaults to port 8159. Docker Compose exposes port 8159 on the
host by default and uses a separate container-internal port. GitHub stores the
source; it does not host this running workspace. Publishing to PyPI is optional.

To run the reproducible container locally:

```bash
docker compose up --build
```

The host port remains bound to `127.0.0.1`. The image uses an explicit
`--allow-non-loopback` flag only inside the container so Uvicorn can listen on
the container interface. Set `FC_AUTH_TOKEN` in the environment for the
built-in single-user web/API and streamable MCP HTTP guard, and still add TLS, an authenticated reverse
proxy and per-user limits before any remote publication.

See the [README](../README.md#try-it-before-configuring-a-model) for the first question and
[configuration example](../.env.example) for optional providers. Install local
embeddings only when needed with `pip install -e '.[semantic]'` in the active
environment. Reproducible historical environment measurements are documented
separately from this installation path.
