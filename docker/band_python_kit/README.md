# Band Python kit for Docker Sandboxes

Run your Python agent on [Band](https://band.ai) inside a Docker
[Sandbox](https://docs.docker.com/ai/sandboxes/): an isolated microVM with a
default-deny egress allowlist, where your project's locked dependencies are
installed automatically and your agent starts headlessly — no manual SDK
installation, no host pollution.

Your workspace stays a plain `uv` project (`pyproject.toml` + committed
`uv.lock`); the kit brings the Band SDK, the launcher, and the network
policy. Tested with `sbx` v0.34.0.

## Quickstart

You need [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) (`sbx`),
Docker, and a registered Band agent (its id and API key).

```bash
# 1. Build the kit image and make it visible to the sandbox runtime
#    (the sandbox VM cannot see host-local Docker images).
docker build -f docker/band_python_kit/Dockerfile -t band-python-kit:local .
docker save band-python-kit:local | sbx template load /dev/stdin

# 2. Start your workspace from the example project.
cp -r docker/band_python_kit/example ~/my-band-agent
cd ~/my-band-agent
#    - set agent.id in band.yaml
#    - put your keys in .band/secrets.env (chmod 600; it is gitignored)

# 3. Create the sandbox — your agent starts immediately.
sbx create --name my-band-agent \
  --kit /path/to/band-sdk-python/docker/band_python_kit \
  band-python-kit ~/my-band-agent
```

Mention your agent in a Band room — it replies from inside the sandbox. The
example echoes messages; swap `EchoAdapter` in `main.py` for any
`band.adapters.*` framework adapter (LangGraph, Anthropic, CrewAI, ...) and
add the matching `band-sdk` extra to your `pyproject.toml`.

## Your workspace

```text
my-band-agent/
├── band.yaml            # agent identity, endpoints, paths (see example/)
├── main.py              # your entrypoint — any program that runs a Band agent
├── pyproject.toml       # your dependencies, including band-sdk
├── uv.lock              # committed lock — resolution never happens at startup
├── .gitignore           # must cover the credential file below
└── .band/
    └── secrets.env      # opt-in plaintext credentials (never commit)
```

The launcher runs `uv sync --locked` with the image's pinned `uv` into a
sandbox-owned environment (never inside your mounted workspace, never into
the SDK's own venv), then executes your entrypoint with that environment's
interpreter. A missing or stale `uv.lock` fails the launch with a clear
error — update it with `uv lock` and recreate.

### Configuration

`band.yaml` is strict: unknown fields fail the launch. See
`example/band.yaml` for the full annotated shape. Environment variables
override the file:

| Variable | Overrides |
|---|---|
| `BAND_AGENT_ID`, `BAND_API_KEY` | agent identity / key |
| `BAND_REST_URL`, `BAND_WS_URL` | Band endpoints (default: production) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` | LLM backend credentials |
| `BAND_KIT_*_PATH` | each configurable path (config, project, entrypoint, credentials, environment, state, cache, log, repository) |

### Credentials

`.band/secrets.env` is an explicit opt-in
(`credentials.acknowledgePlaintextInSandbox: true` in `band.yaml`): the
plaintext keys exist in both your workspace and the sandbox VM. The launcher
enforces the guardrails — the file must be gitignored, never Git-tracked,
owner-only (`chmod 600`), not a symlink, and may only define documented
credential names. Values already present in the environment always win; the
file only fills gaps. Never commit it.

## How the launch works

At every sandbox start (creation and each restart — no attach session
needed), the kit's startup command runs the image entrypoint as root to
install the sandbox's per-session proxy CA, drops to the non-root `agent`
user (uid 1000), and hands off to the Band launcher, which:

1. loads `band.yaml` and environment overrides (strict validation),
2. optionally loads missing credentials from your opt-in env file,
3. validates every path (no traversal, no symlink escapes, runtime storage
   outside the workspace and SDK venv),
4. optionally initializes a configured repository,
5. syncs your locked dependencies into the sandbox-owned environment, and
6. replaces itself with your entrypoint (`os.execve`) — signals like
   `sbx stop`'s SIGTERM reach your code directly (the example handles them
   with `band.runtime.shutdown.run_with_graceful_shutdown`).

Troubleshooting: startup output lands in `/var/log/sbx-kit-startup.log`
inside the sandbox, launcher diagnostics under your configured
`runtime.logPath`, and `sbx policy log <sandbox>` shows every allowed and
blocked network request. Launch errors name their failing phase (`[config]`,
`[credentials]`, `[paths]`, `[sync]`, ...) and never contain secret values.

## Network access

The kit allows only what the launch flow and the supported LLM backends
(OpenAI, Anthropic/Claude, GitHub Copilot) need: Band, PyPI, and each
backend's API hosts. Everything else is denied by the sandbox proxy.

To reach an additional host — or a non-production Band deployment — grant it
per sandbox instead of editing the kit:

```bash
sbx policy allow network --sandbox my-band-agent platform.dev.band.ai
```

Do not set `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` yourself: the sandbox
runtime manages them, and overriding them bypasses the network policy.

Kit changes apply only at creation (`--kit` on `sbx create`); to pick up a
modified kit, remove and recreate the sandbox. Do not rely on post-create
`sbx kit add`: it applies files and startup commands but silently skips the
kit's network and credential configuration.

## Image reference

### Build variants

The default build bakes the core Band SDK only. To pre-bake one framework
extra into the image (your workspace venv is unaffected either way):

```bash
docker build -f docker/band_python_kit/Dockerfile \
  --build-arg SDK_EXTRA=langgraph \
  -t band-python-kit:langgraph .
```

`SDK_EXTRA` accepts any single extra from `pyproject.toml`'s
`[project.optional-dependencies]`. One at a time — some extras have
conflicting dependencies and cannot share a venv (e.g. `crewai` conflicts
with `parlant`/`pydantic_ai`). Multi-arch builds work with
`docker buildx build --platform linux/amd64,linux/arm64`.

### Layout

| Path / env var | What it is |
|---|---|
| `$BAND_SDK_HOME` (`/opt/band`) | Root of the baked SDK install. Read-only to every user, including root, after build. |
| `$BAND_SDK_PYTHON` (`/opt/band/venv/bin/python`) | Fixed interpreter for the Band SDK and launcher. **Not on `PATH`** — it can never shadow your project's own venv. |
| `$BAND_SDK_UV` (`/opt/band/bin/uv`) | The build's digest-pinned `uv`, used for the locked dependency sync. **Not on `PATH`**, read-only, never downloaded or upgraded at runtime. |
| `agent` (uid 1000, `$HOME=/home/agent`) | The non-root user your agent runs as. |

### CA trust and privilege drop

Docker Sandboxes generates a per-session proxy CA and passes it to the
container as base64 in `PROXY_CA_CERT_B64`. The entrypoint decodes it into
the system trust store (`update-ca-certificates` needs root, which is why
the container starts as root) and then always drops to `agent` (uid 1000)
via `setpriv` before executing anything else — nothing of yours ever runs
as root. To verify the drop, inspect the process table (`docker top`, or
`ps` inside the sandbox) rather than `docker exec ... whoami`: a fresh
`exec` process defaults to root regardless of what PID 1 dropped to.

`SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` are baked in pointing at the
*whole* system bundle so `httpx` (which otherwise trusts only its vendored
certifi bundle) sees the proxy CA too. Never override them to a narrower
CA file — public TLS verification must keep working alongside the proxy.
When `PROXY_CA_CERT_B64` is unset (e.g. a plain `docker run` outside a
sandbox), the install step is skipped and standard TLS verification applies.

### Running the image directly

```bash
docker run --rm band-python-kit:local \
  bash -c '$BAND_SDK_PYTHON -c "import band; print(band.__version__)"'
```
