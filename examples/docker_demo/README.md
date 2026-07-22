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
| `launch.sh` | One-command build / up / down; owns a `.demo/run` manifest |
| `Dockerfile.cli` | PM + Dev kit image = base kit + the `claude` and `codex` CLIs |
| `agents/{pm,dev,architect}/` | Per-agent sbx workspace (`main.py`, `band.yaml`, `pyproject.toml`, `uv.lock`, `prompt.md`) |
| `skill/SKILL.md` | Presenter-facing skill (quick start) |

## Prerequisites

- `sbx` ≥ 0.35.0, signed in (`sbx login`); Docker; `uv`.
- Host keys: `BAND_API_KEY_USER` (a Band **user** key — the conductor and
  presenter identity), `ANTHROPIC_API_KEY` (PM), `OPENAI_API_KEY` (Dev + Architect).

## Run

```bash
cd examples/docker_demo
cp .demo.env.example .demo.env    # fill in keys + (optional) endpoints; auto-loaded

./launch.sh build     # once: needs Docker + sbx only (no keys); builds + loads images
./launch.sh up        # provisions agents, creates 3 sandboxes, runs the meeting
./launch.sh down      # removes exactly what the last run recorded (also runs on exit)
```

Keys can live in `.demo.env` or the environment (`BAND_API_KEY_USER`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Non-production Band: set `BAND_REST_URL` /
`BAND_WS_URL` (endpoints, host, and each `band.yaml` are derived from them).

## Expected output

1. `build` produces `band-python-kit:local` and `band-python-kit-cli:local` and
   loads both as sbx templates.
2. `up` registers Maya/Sam/Jordan, grants global egress, then for each agent:
   injects the LLM + Band credentials host-side, creates the sandbox, and streams
   its **setup log to a labeled pane**.
3. The conductor creates the room and posts the brief. Maya and Sam discuss;
   Maya invites Jordan; Jordan posts a `DECISION:`; the conductor closes the room.
4. Cleanup removes exactly the sandboxes, secrets, policy rules, and agents this
   run recorded.

## The never-in-VM proof

With a sandbox up:

```bash
sbx secret ls band-demo-pm                        # host holds the real keys
sbx exec band-demo-pm env | grep -iE 'API_KEY'    # in the VM: only placeholders
```

The VM holds only placeholders — `proxy-managed` for the Band key, an
`sk-…`-shaped sentinel for the LLM key (under `ANTHROPIC_PROXY_KEY` /
`OPENAI_PROXY_KEY`, which each agent copies into the real var for its CLI). The
sbx proxy swaps the placeholder for the real key on outbound requests to the Band
and provider hosts (verifiable via the `O=Docker Sandboxes` MITM cert). The kit
launcher **fails the launch** if a real Band key is found in the VM.

## Circuit breaker

The conductor guarantees the meeting ends (see `breaker.py` for the state machine
and `../../tests/example_agents/test_docker_demo_breaker.py` for its offline
tests). Tiers:

- **Nudge** — after `DEMO_SOFT_CAP` PM↔Dev messages with no handoff, nudge Maya to
  bring in Jordan.
- **Add-fallback** — `DEMO_HANDOFF_DEADLINE` further messages with Jordan still
  absent, add her ourselves (Maya's invite never landed).
- **Hard-kill** — no decision yet and `DEMO_HARD_CAP` agent messages or
  `DEMO_WALL_CLOCK_S` elapsed.
- **Clean end** — Jordan posts a `DECISION:`; `DEMO_GRACE_S` later, close and stop.
  A decided meeting is never hard-killed, so it can run to `wall_clock + grace`.

Only **agent** messages move the caps — your interjections never trip the breaker.
Timers run on the conductor's clock (not platform timestamps). All caps env-tunable.

## Reset / re-run

`./launch.sh down` removes only what the last run recorded (sandboxes, scoped
secrets, global egress rules, agents) — never an unrelated `band-demo-*` — and
reports any resource it couldn't remove. Teardown also runs on `up` exit, so a
normal run leaves a clean host. `build` is only needed once or after a kit change.

## Status

Validated live on dev: never-in-VM inference for both CLIs (claude + codex
authenticate through the proxy via a placeholder; codex via `codex login`),
provisioning, egress, the circuit breaker (wall-clock kill fired), and cleanup.

Confirm before the show:

1. **Full happy path** — PM+Dev align, Maya hands off, Jordan posts a `DECISION:`,
   clean end. The pieces are proven individually; the end-to-end conversation is
   the last rehearsal step. The Architect (CrewAI) uses the same OpenAI-over-HTTPS
   path codex proved.
2. **codex transport** — codex reaches `api.openai.com` over an HTTPS fallback (its
   WebSocket transport is blocked by the MITM proxy): functional, but noisy in the
   logs.

## Testing

The breaker and the conductor's message projections are unit-tested offline (no
platform, no network):

```bash
uv run pytest tests/example_agents/test_docker_demo_breaker.py \
              tests/example_agents/test_docker_demo_conductor.py -v --no-cov
```
