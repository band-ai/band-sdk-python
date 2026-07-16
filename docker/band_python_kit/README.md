# band-python-kit base image

Base image for the Band Docker Sandboxes kit: a customer's existing framework
agent (LangGraph, CrewAI, Anthropic, etc.) boots inside a sandbox already
connected to Band. This image provides the immutable Band SDK layer; the
customer's own framework lives in a separate venv created at sandbox runtime
(not part of this image).

The publishing workflow is a separate release deliverable. This README covers
building and running the image directly.

## Build

Core SDK only, no framework extras:

```bash
docker build -f docker/band_python_kit/Dockerfile -t band-python-kit .
```

Bake in exactly one framework extra (e.g. for a kit variant dedicated to a
single framework):

```bash
docker build -f docker/band_python_kit/Dockerfile \
  --build-arg SDK_EXTRA=langgraph \
  -t band-python-kit:langgraph .
```

`SDK_EXTRA` accepts any single extra from `pyproject.toml`'s
`[project.optional-dependencies]` (`langgraph`, `anthropic`, `claude_sdk`,
`crewai`, `pydantic_ai`, ...). Only one at a time: some extras conflict and
cannot resolve into the same venv (`crewai` vs. `parlant`/`pydantic_ai` — see
the Dependency Conflicts section of the repo's `CLAUDE.md`). Any framework not
baked in here is installed into the customer's own venv at sandbox runtime,
isolated from this one.

Multi-arch (arm64 + x86_64):

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/band_python_kit/Dockerfile -t band-python-kit .
```

## Image layout

| Path / env var | What it is |
|---|---|
| `$BAND_SDK_HOME` (`/opt/band`) | Root of the baked SDK install. Read-only to every user, including root, after build (`chmod -R a-w`). |
| `$BAND_SDK_PYTHON` (`/opt/band/venv/bin/python`) | Fixed, absolute interpreter for the Band SDK. **Not on `PATH`** — the SDK venv can never shadow the customer's own venv or interpreter. Invoke the SDK only via this path. |
| `agent` (uid 1000, `$HOME=/home/agent`) | The non-root user every process ends up running as. Matches the sandbox's `$HOME`-mounted workspace convention. |

## CA trust

Docker Sandboxes generates a per-session proxy CA and exposes it to the
container as base64 in `PROXY_CA_CERT_B64`. The entrypoint:

1. Decodes `PROXY_CA_CERT_B64` (if set) into
   `/usr/local/share/ca-certificates/sandbox-proxy-ca.crt` and runs
   `update-ca-certificates` — this needs root, which is why the container
   starts as root before dropping privileges (see below).
2. Relies on `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`, baked in as image `ENV`
   and pointed at the system bundle
   (`/etc/ssl/certs/ca-certificates.crt`) — this is what `httpx` needs,
   since it trusts only its vendored `certifi` bundle by default and
   ignores the system trust store otherwise. `websockets` already defaults
   to `ssl.create_default_context()`, so it needs no extra wiring.

Both are additive (pointed at the *whole* system bundle, not a narrowed,
kit-private CA file) — never override these to trust only an internal CA;
that breaks the credential proxy's own TLS termination path.

If `PROXY_CA_CERT_B64` is unset (plain allowlisted egress, no credential
injection), the entrypoint skips the install step entirely and every real,
publicly-trusted upstream cert still verifies normally.

## Privilege drop

The container's default process starts as **root** — required for the CA
install step above, which only root can perform. The entrypoint
(`entrypoint.sh`) always ends by dropping to `agent` (uid 1000) via
`setpriv` before `exec`-ing the real command:

```bash
exec setpriv --reuid=agent --regid=agent --init-groups -- "$@"
```

Nothing meant to run as the sandbox's agent user ever executes as root —
verify with `docker top <container>`, not `docker exec ... whoami` (a fresh
`docker exec` process defaults to root regardless of what PID 1 dropped to).

## Run

```bash
docker run --rm band-python-kit bash -c '$BAND_SDK_PYTHON -c "import band; print(band.__version__)"'
```

With a proxy CA (mirrors what a real sandbox session sets):

```bash
docker run --rm -e PROXY_CA_CERT_B64="$(base64 -i proxy-ca.pem)" band-python-kit \
  bash -c '$BAND_SDK_PYTHON -c "import httpx; print(httpx.get(\"https://your-injected-domain\").status_code)"'
```

## Out of scope here

- **Customer venv creation** — created at sandbox runtime from the
  workspace's own dependency declaration, not part of this image.
- **`spec.yaml`** (`kind: sandbox`, `caps.network.allow`, credentials) — a
  separate deliverable; this image is what it points at.
- **`sbx kit validate` / `sbx run --kit`** — requires the `spec.yaml` above
  and a real Docker Sandboxes-capable machine; not runnable in CI (no
  nested virtualization on GitHub-hosted runners).
