# Band × Docker — three-agent design-meeting demo

A live, watchable design meeting run from the Band Docker kit: three AI agents,
each built with a **different framework** and running in its **own Docker
sandbox (sbx)**, collaborating in one Band room. The demo's spine is
**credential custody** — every agent's API keys stay on the host and are
injected on the wire, so they are never present inside any VM.

## Cast

| Agent | Name | Framework | Sandbox | LLM (host-held) |
|---|---|---|---|---|
| PM / team lead | Maya | Claude SDK | `band-demo-pm` | Anthropic |
| Lead Developer | Sam | Codex | `band-demo-dev` | OpenAI |
| Software Architect | Jordan | CrewAI | `band-demo-architect` | OpenAI |

They design **a URL shortener**. Maya and Sam discuss and align; Maya invites
Jordan, who reviews and returns one decision. A host-side **conductor** enforces
a circuit breaker so the conversation always ends. You (the presenter) are a
human participant and can interject at any time by @mentioning an agent.

## Architecture

```
              host (your laptop)                         Docker sandboxes (VMs)
  ┌──────────────────────────────────┐        ┌───────────────────────────────┐
  │ conductor.py + circuit breaker    │        │  band-demo-pm    (Claude SDK) │
  │  · creates the Band room          │  Band  │  band-demo-dev   (Codex)      │
  │  · kicks off the design           │ ◀────▶ │  band-demo-architect (CrewAI) │
  │  · polls messages, enforces caps  │  room  │                               │
  │  · nudge / add-fallback / stop    │        │  each: kit launcher → agent   │
  │ real API keys (proxy-injected) ───┼────────┼──▶ VM sees only "proxy-managed"│
  └──────────────────────────────────┘        └───────────────────────────────┘
```

## Files

| Path | Purpose |
|---|---|
| `breaker.py` | Pure conversation circuit breaker (no IO); the safety piece |
| `conductor.py` | Host-side room driver that enforces the breaker |
| `provision.py` | Register / tear down the three agents (Human API) |
| `launch.sh` | One-command build / up / down orchestration |
| `Dockerfile.codex` | Developer kit image = base kit + the `codex` CLI |
| `agents/{pm,dev,architect}/` | Per-agent sbx workspace (`main.py`, `band.yaml`, `pyproject.toml`, `uv.lock`, `prompt.md`) |
| `skill/SKILL.md` | Presenter-facing skill (quick start) |

## Prerequisites

- `sbx` ≥ 0.35.0, signed in (`sbx login`); Docker; `uv`.
- Host keys: `BAND_API_KEY_USER` (a Band **user** key — the conductor and
  presenter identity), `ANTHROPIC_API_KEY` (PM), `OPENAI_API_KEY` (Dev + Architect).

## Run

```bash
cd examples/docker_demo
export BAND_API_KEY_USER=band_u_... ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-...

./launch.sh build     # once: builds + loads the base and codex kit images
./launch.sh up        # provisions agents, creates 3 sandboxes, runs the meeting
./launch.sh down      # tears down sandboxes + provisioned agents (also on exit)
```

Non-production Band: set `BAND_REST_URL` / `BAND_WS_URL` (and `BAND_SECRET_HOST`,
the proxy wildcard, e.g. `**.staging.band.ai`) before `up`.

## Expected output

1. `build` produces `band-python-kit-demo:local` and `band-python-kit-codex:local`
   and loads both as sbx templates.
2. `up` registers Maya/Sam/Jordan, writes `agent_config.yaml` + `.demo/agents.env`,
   then for each agent: creates the sandbox, grants egress, injects the LLM +
   Band credentials host-side, and streams its **setup log to a labeled pane**.
3. The conductor creates the room and posts the brief. Maya and Sam discuss;
   Maya invites Jordan; Jordan posts a verdict; the conductor closes the room.
4. Teardown removes the sandboxes and deletes the provisioned agents.

## The never-in-VM proof

With a sandbox up:

```bash
sbx secret ls band-demo-pm                          # host holds the real key
sbx exec band-demo-pm env | grep -i BAND_API_KEY    # in the VM: only "proxy-managed"
```

The VM only ever holds the `proxy-managed` sentinel; the sbx proxy swaps the real
key onto outbound `**.band.ai` requests (verifiable via the `O=Docker Sandboxes`
MITM cert). The kit launcher **fails the launch** if a real Band key is found in
the VM under proxy-managed custody.

## Circuit breaker

The conductor guarantees the meeting ends (see `breaker.py` for the state machine
and `../../tests/example_agents/test_docker_demo_breaker.py` for its offline
tests). Tiers:

- **Soft** — after `DEMO_SOFT_CAP` PM↔Dev messages with no handoff, nudge Maya to
  bring in Jordan (and add Jordan as a fallback if she hasn't).
- **Hard** — at `DEMO_HARD_CAP` agent messages or `DEMO_WALL_CLOCK_S`, force-stop.
- **Clean end** — `DEMO_GRACE_S` after Jordan's decision, close and stop.

Only **agent** messages move the caps — your interjections never trip the breaker.
All caps are env-tunable before a show.

## Reset / re-run

`./launch.sh down` (or just exit `up` — teardown runs on exit) returns to a clean
state; `./launch.sh up` again is a fresh run. `build` is only needed once or after
a kit change.

## Rehearsal-gated confirmations

This demo drives live `sbx` and two coding-agent CLIs; confirm these against the
installed toolchain during rehearsal (each is marked `CONFIRM AT REHEARSAL` in
`launch.sh`):

1. **sbx flags** — `sbx create --kit … band-python-kit <workspace>`, the
   per-sandbox secret scope flag (`--sandbox`), and `set-custom` options track the
   kit README for sbx 0.35.0; verify on your version.
2. **Setup-log streaming** — the labeled pane tails `/var/log/sbx-kit-startup.log`
   via `sbx exec`; confirm the path/command, or switch to `sbx logs`.
3. **Codex CLI** — the Developer image installs `@openai/codex`; confirm `codex
   app-server` runs under the `agent` user with `CODEX_HOME=/home/agent/.codex`.
4. **LLM proxy custody** — confirm the Anthropic/OpenAI clients issue requests
   with sbx's built-in provider injection (they may need a placeholder key in env
   to attempt the call, which the proxy then replaces). This is the one path the
   kit's own live proof (`tests/docker/test_kit_proxy_managed_live.py`) still lists
   as "run live against staging".

## Testing

The breaker and the conductor's message projections are unit-tested offline (no
platform, no network):

```bash
uv run pytest tests/example_agents/test_docker_demo_breaker.py \
              tests/example_agents/test_docker_demo_conductor.py -v --no-cov
```
