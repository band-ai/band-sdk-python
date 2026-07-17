# Band Python kit for Docker Sandboxes

Run your Python agent on [Band](https://band.ai) inside a Docker
[Sandbox](https://docs.docker.com/ai/sandboxes/): an isolated microVM with a
default-deny egress allowlist, where your project's locked dependencies are
installed automatically and your agent starts headlessly — no manual SDK
installation, no host pollution.

Your workspace stays a plain `uv` project (`pyproject.toml` + committed
`uv.lock`); the kit brings the Band SDK, the launcher, and the network
policy. Tested with `sbx` v0.34.0.

## Why use this kit?

It turns adopting Band into a small application task instead of an
infrastructure project. You bring your agent code, its locked Python
dependencies, and your Band credentials. The kit provides the SDK, a
repeatable startup process, and sandbox network safeguards.

There are two separate Python environments inside the sandbox:

- The kit environment runs the stable launcher. It validates configuration,
  prepares the agent environment, and reports startup failures.
- Your agent environment contains the dependencies from your project's
  `uv.lock` and runs your `main.py`.

This means changing your agent's adapter or packages cannot overwrite the
launcher that starts it. If a dependency update fails, the launcher is still
available to show the error and rebuild the agent environment on the next
start. Your host workspace remains source code and configuration rather than
a collection of sandbox-created virtualenv files, caches, and logs.

## Quickstart

You need [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) (`sbx`),
Docker, and a registered Band agent (its id and API key).

```bash
# 1. Build the kit image and make it visible to the sandbox runtime
#    (the sandbox VM cannot see host-local Docker images).
docker build -f docker/band_python_kit/Dockerfile -t band-python-kit:local .
docker save band-python-kit:local | sbx template load /dev/stdin

# 2. Start your workspace from the example project (see example/README.md).
cp -r docker/band_python_kit/example ~/my-band-agent
cd ~/my-band-agent
#    - set agent.id in band.yaml
#    - create .band/secrets.env from secrets.env.example (chmod 600)

# 3. Create the sandbox — your agent starts immediately.
sbx create --name my-band-agent \
  --kit /path/to/band-sdk-python/docker/band_python_kit \
  band-python-kit ~/my-band-agent
```

Mention your agent in a Band room — it replies from inside the sandbox. To
make the example your own — swap the echo adapter for a real framework
adapter, set up credentials — see
[`example/README.md`](example/README.md).

## Your workspace

Your workspace is a plain `uv` project plus `band.yaml`;
[`example/`](example/README.md) is a complete template with the file map
and authoring guide.

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
| `BAND_KIT_CONFIG_PATH` | where to find `band.yaml` (every other path is configured inside it) |

### Credentials

`.band/secrets.env` is an explicit opt-in
(`credentials.acknowledgePlaintextInSandbox: true` in `band.yaml`): the
plaintext keys exist in both your workspace and the sandbox VM, and the
launcher enforces the guardrails around the file. Setup and the accepted
names: [`example/README.md`](example/README.md#credentials) and its
`secrets.env.example`.

## How the launch works

At every sandbox start (creation and each restart — no attach session
needed), the kit's startup command runs the image entrypoint as root to
install the sandbox's per-session proxy CA, drops to the non-root `agent`
user (uid 1000), and hands off to the Band launcher, which:

1. loads `band.yaml` and environment overrides (strict validation),
2. optionally loads missing credentials from your opt-in env file,
3. validates every path (no traversal, no symlink escapes, runtime storage
   outside the workspace and SDK venv),
4. optionally clones your project from Git into the project path (the
   `repo:` section in `band.yaml` — see the annotated example; existing
   checkouts are validated and reused, never re-cloned),
5. syncs your locked dependencies into the sandbox-owned environment, and
6. replaces itself with your entrypoint (`os.execve`) — signals like
   `sbx stop`'s SIGTERM reach your code directly (the example handles them
   with `band.runtime.shutdown.run_with_graceful_shutdown`).

Troubleshooting: startup output lands in `/var/log/sbx-kit-startup.log`
inside the sandbox, launcher diagnostics under your configured
`runtime.logPath`, and `sbx policy log <sandbox>` shows every allowed and
blocked network request. Launch errors name their failing phase (`[config]`,
`[credentials]`, `[paths]`, `[repo]`, `[sync]`, ...) and never contain
secret values.

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
| `$BAND_SDK_PYTHON` (`/opt/band/venv/bin/python`) | Fixed interpreter for the Band SDK and launcher. **Not on `PATH`** — it can never shadow your project's own venv. Invoke the SDK only via this path. |
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
certifi bundle) sees the proxy CA too; `websockets` already defaults to
`ssl.create_default_context()` and needs no extra wiring. Never override
them to a narrower CA file — public TLS verification must keep working
alongside the proxy.
When `PROXY_CA_CERT_B64` is unset (e.g. a plain `docker run` outside a
sandbox), the install step is skipped and standard TLS verification applies.

### Running the image directly

```bash
docker run --rm band-python-kit:local \
  bash -c '$BAND_SDK_PYTHON -c "import band; print(band.__version__)"'
```
