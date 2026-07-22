# Docker Sandbox staging smoke

A reproducible **manual** smoke: a headless `band-sdk` agent runs inside a real
[Docker Sandbox](https://docs.docker.com/ai/sandboxes/) (`sbx exec`) against a
staging Band deployment. It proves WebSocket message receipt, a REST reply, and
— because the operator manually cycles host Wi-Fi while the agent process
keeps running — reconnect behavior behind Docker's proxy.

This is **not** an automated E2E test. Docker Sandbox policy changes do not
close an already-open WebSocket tunnel, and there is no supported
socket-only fault-injection command, so the network interruption is performed
by a human. The workflow is a two-phase, resumable script: it must not block
through the outage, since turning Wi-Fi off can disconnect the operator too.

## What this proves

See `skill/references/requirements.md` for the full requirement-to-evidence
mapping.

## Prerequisites (one-time)

```bash
brew install docker/tap/sbx        # or see docs.docker.com/ai/sandboxes
sbx login                          # sign in to Docker (interactive)
sbx policy init balanced           # default-deny + common dev/package APIs
uv sync --extra dev                # from the repo root
```

Every script here except `agent.py` runs inside this repository's own dev
environment, not as a standalone PEP 723 script — they need
`tests/e2e/baseline`, which is dev-only source in this repo, not part of the
published `band-sdk` package (see **How this reuses the SDK's E2E toolkit**
below). `agent.py` is the one exception: it must work as a standalone example
against just the published package, since it runs inside the sandbox as a
customer-style workspace.

You also need, outside source control:

- a staging REST URL and WebSocket URL (never production — every script here
  rejects the SDK's own production defaults outright) and a staging user/test
  key (`BAND_API_KEY_USER`) — this is the *only* Band credential you supply,
  since the sandboxed agent's own identity is minted fresh each run (see
  below). Add these three to the repo root's `.env.test` — the same file the
  rest of the E2E baseline toolkit already uses — rather than a file local to
  this directory;
- `SBX_SANDBOX` (a sandbox name) and `SBX_WORKSPACE` (a disposable workspace
  path under `$HOME` — **not** an active checkout of this repository, since
  the default sandbox mount is read/write and would let the sandbox edit your
  working tree live). These are `sbx`-specific, not Band platform config, so
  `export` them in your shell rather than adding them to `.env.test`.

## Two ways to run it

| Runner | Uses | When to choose it |
|---|---|---|
| Operator | This README plus `setup.sh`, `run.sh`, and `probe.py` | Reproduce or debug the smoke without an AI client. |
| Codex / Claude Code | `skill/SKILL.md`, which orchestrates those same files | Guided progress updates, resume support, and report generation. |

Both paths write the same `.sandbox-smoke/` state and `evidence.md`; neither is
a second implementation of the scenario.

To use the skill from an AI client, symlink it into the client's discovery
location (a one-time, idempotent step — `preflight.py` reports a missing or
stale link):

| Client | Discovery symlink |
|---|---|
| Codex | `$CODEX_HOME/skills/band-sandbox-staging-smoke` → `skill/` |
| Claude Code | `.claude/skills/band-sandbox-staging-smoke` (or `~/.claude/skills/...`) → `skill/` |
| Any other client | Follow this README and run the scripts directly. |

## Operator flow

### 1. Preflight and sandbox creation

```bash
export SBX_SANDBOX=my-sandbox-name
export SBX_WORKSPACE=$HOME/disposable-workspace
./setup.sh
```

Runs `skill/scripts/preflight.py` first (the one place every safety check —
`sbx` installed, staging endpoints set and non-production, workspace genuinely
outside this checkout — lives), then creates (or reuses) a disposable
sandbox, allowlists the staging host plus the package hosts needed to
bootstrap (scoped to this sandbox only), copies `agent.py` into the sandbox
workspace, and warms its `uv`-managed environment. It does **not** touch the
Band platform — no agent/room is created yet.

### 2. Start the headless agent

```bash
./run.sh
```

Keep this terminal open. `run.sh` provisions a fresh Band agent + room
(`probe.py --label provision` — see below), then runs the agent with
`sbx exec`, logging connection, disconnect, and reconnect activity to
`.sandbox-smoke/agent.log`. Wait for its readiness line.

### 3. Prove the initial round trip

In a second terminal:

```bash
uv run probe.py --label initial
```

Sends a unique marker as the staging user (mentioning the agent) and waits for
`sandbox-ack:<marker>` — the same reply barrier (`wait_for_reply`) every
baseline E2E test in this repo uses. Exits non-zero on timeout or a wrong
reply.

### 4. Manually interrupt network and verify reconnect

While `run.sh` keeps running:

1. turn the host Wi-Fi off;
2. wait until `.sandbox-smoke/agent.log` shows the WebSocket disconnect;
3. turn Wi-Fi back on;
4. wait until the log shows reconnect/rejoin; then

   ```bash
   uv run probe.py --label after-wifi-reconnect
   ```

Success requires the second marker reply from the *same* running agent
process — real, manual, real-network validation of the SDK's reconnect
behavior behind Docker's proxy. `probe.py` can be rerun after a transient
failure (e.g. a timeout while the agent was still warming up) — the evidence
report reflects the *latest* attempt per step, not every historical one.

### 5. Sleep/wake and daemon restart (mandatory)

The full run has no optional checks — a report missing either probe renders
INCOMPLETE.

**Sleep/wake** (observed live: VM and agent both survive; the SDK reconnects
on wake): put the host to sleep for ~1 minute, wake it, confirm the agent log
shows a reconnect, then:

```bash
uv run probe.py --label after-sleep-wake
uv run skill/scripts/record-observation.py sleep_wake \
  "<did the VM/process survive; how did it recover>"
```

**Daemon restart** (observed live: the sandbox VM stops and the agent dies
with the daemon — its `sbx exec` attach is severed and the never-persisted
credentials die with the process — so recovery is a full re-provision).
Recording the `daemon-restart` phase first is what authorizes the re-run of
`run.sh` to reuse this run (reaping the dead agent/room itself) instead of
rotating to a fresh one:

```bash
uv run skill/scripts/record-phase.py daemon-restart
sbx daemon stop
sbx daemon start -d                  # -d: detached, not foreground
./run.sh                             # recovery: reaps the dead agent, re-provisions, relaunches
uv run probe.py --label after-daemon-restart   # once the readiness line appears
uv run skill/scripts/record-observation.py daemon_restart \
  "<survived? auto-resumed? what recovery took>"
```

The observations are the *behavior* (survived? auto-resumed? what recovery
took) — the report prints them alongside the probe verdicts, and renders
"(not recorded)" visibly when one is missing.

### 6. Diagnostics and cleanup

On failure, keep only redacted `.sandbox-smoke/agent.log` excerpts and
`sbx policy log` output — never keys, headers, or config/env dumps.

```bash
uv run probe.py --label cleanup     # deletes the provisioned Band room + agent, ends the run
uv run skill/scripts/render-report.py   # writes .sandbox-smoke/evidence.md
```

Then stop the agent (`Ctrl-C` in `run.sh`'s terminal), remove the disposable
sandbox, and delete its workspace. A finished (or abandoned) run's state is
archived automatically the next time a run starts — a new smoke can never
silently inherit a previous run's results.

## How this reuses the SDK's E2E toolkit

`tests/e2e/baseline/README.md` states its `toolkit/` modules are "pytest-free
and reusable anywhere." `probe.py` (this directory) builds on them directly
instead of hand-rolling REST/WebSocket calls or requiring a static
pre-provisioned staging agent:

- **`ResourceManager.provision_agent`** mints the sandboxed agent's identity
  fresh each run (its own id + api_key) and reaps it on cleanup (including a
  failed provisioning attempt, which reaps immediately rather than leaking a
  live agent) — there is no standing staging agent to source, rotate, or leak.
- **`UserOps.send_message`** sends the mention-required probe message.
- **`reply_capture` / `wait_for_reply`** observe the room over a real
  WebSocket connection and barrier on the agent's reply — the exact
  distinction between "turn processed" and "reply frame actually captured"
  that every baseline test relies on.

`probe.py` runs from a full clone of this repository (it imports
`tests.e2e.baseline.*`; `state.repo_root()` finds the repo root, fixed by this
example's own location in the tree), unlike `agent.py`, which must still work
as a standalone example against just the published `band-sdk` package, since
it runs inside the sandbox as a customer-style workspace.

## Evidence report

`.sandbox-smoke/` is gitignored and holds durable, redacted evidence:

- `state.json` — run id, phase, timestamps, sandbox name, version, and
  probe/residual-check results; never credentials.
- `agent.log` — the sandboxed agent's own log (connect/disconnect/reconnect).
- `evidence.md` — the rendered PASS/FAIL/INCOMPLETE report, ready to attach to
  the work item or copy to your team's evidence store.

## Design notes

- Verified locally against the installed `sbx` v0.34.0: `sbx exec` starts a
  stopped sandbox and runs commands headlessly, and takes real `-e KEY=value`
  and `--workdir` flags (mirroring `docker exec`) — no need to shell out to a
  bare `env` command. `sbx create shell PATH` is the right form for a plain
  Python workload (sbx's other `create` subcommands are specific coding-agent
  environments). `sbx policy allow network --sandbox NAME "host1,host2"`
  scopes the allowlist to one sandbox in a single call. `sbx ls --json`
  (`ls` is the documented subcommand; `list` also works but is an undocumented
  alias) gives a structured way to check whether a sandbox already exists,
  instead of pattern-matching table output.
- Also verified: `sbx policy` changes do not tear down an already-open
  WebSocket tunnel (confirmed by direct experiment), so the network
  interruption in step 4 has to be a real, manual Wi-Fi cycle.
- Discovered via a real sandbox run: installing `band-sdk` from this repo's
  own `git+https://...` source (the pattern most other examples in this repo
  use) fails inside the sandbox — `uv`/`pip`'s git install does a full clone,
  and this repo carries a private, SSH-only `.claude` submodule the sandbox
  has no credentials for, so the clone's submodule step fails even though
  `.claude` has nothing to do with the `band` package. `agent.py` and this
  script install `band-sdk[langgraph]` from PyPI instead, which needs no
  clone at all.
- Discovered via a real sandbox run (the smoke's first genuinely valuable
  catch): the sandbox proxy answers `CONNECT` with `HTTP/1.0 200`, which
  `websockets <= 15.0.1` rejects outright (`InvalidProxyMessage: did not
  receive a valid HTTP response from proxy`; fixed in websockets 16.0, and no
  fixed 15.x exists) — while REST keeps working, since `httpx` tolerates
  HTTP/1.0. `langgraph-sdk` pins `websockets<16`, forcing the broken version
  onto every `band-sdk[langgraph]` install. So *any* langgraph-based band
  agent behind an HTTP/1.0-answering proxy (Docker Sandbox's included, and
  many corporate proxies) can never open its WebSocket. `agent.py` carries a
  `[tool.uv] override-dependencies = ["websockets>=16"]` as the workaround —
  safe because the deterministic graph never uses langgraph-sdk's own
  WebSocket client, which is all that pin protects. Worth an upstream fix:
  `phoenix-channels-python-client` declares `websockets>=10.0`, far below
  what proxy support actually requires.
- Sandbox CLI behavior changes between releases — re-check `sbx --version`
  and `sbx <command> --help` before relying on any of the above after
  upgrading.
