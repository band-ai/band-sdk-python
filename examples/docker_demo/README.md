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
  │  · nudge / add / open floor / stop│        │  each: kit launcher → agent   │
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

## Prerequisites

- `sbx` ≥ 0.35.0, signed in (`sbx login`); Docker; `uv`.
- `tmux` (recommended) — the launcher opens one window with a live log pane per
  agent inside your current terminal. Without it, it falls back to spawning
  separate Terminal.app windows (macOS) or prints the tail commands.
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
`BAND_WS_URL` (endpoints, host, and each `band.yaml` are derived from them). To load a
different env file instead of `.demo.env`, point `DEMO_ENV_FILE` at it:

```bash
DEMO_ENV_FILE=/path/to/.env ./launch.sh up
```

### Watch it run

`up` gives you two views:

- **The Band UI.** The conductor creates a room titled `Design Review — <topic> — <date time>`
  and the launcher **opens it in your browser** automatically (URL also logged as
  `Room UI URL:` and written to `.demo/room.url`). Override the link shape with
  `DEMO_UI_URL_TEMPLATE` (a `{chat_id}` template); it defaults to `<BAND_REST_URL>/chat/{chat_id}`.
- **Per-agent logs.** With `tmux`, the launcher starts a `band-demo` session with one
  live setup/log pane per agent. Attach from another terminal tab:

  ```bash
  tmux attach -t band-demo        # detach with Ctrl-b then d
  ```

  Without `tmux` it falls back to separate Terminal.app windows (macOS) or just prints
  the `sbx exec … tail -f` command per agent.

### Ending the meeting

An interactive `up` (the default) hands you the room after the verdict — chat with the
agents, then end it any of three ways, **each of which runs cleanup**:

- post an **end phrase** in the room — `end meeting`, `/end`, `wrap up`, or `adjourn`
  (works at any point, even mid-discussion);
- go **idle** for `DEMO_OPEN_FLOOR_IDLE_S` (default 7 min);
- press **Ctrl-C**.

For an unattended run (CI / recording), set `DEMO_HEADLESS=1` on `up`: the meeting
skips the open floor and closes on the verdict, so it never waits on a presenter.

### Tuning

Every breaker ceiling and the topic are env-overridable — retune before a show without
touching code:

| Var | Default | Effect |
|---|---|---|
| `DEMO_TOPIC` | `a URL shortener service` | What the agents design |
| `DEMO_SOFT_CAP` | `10` | PM↔Dev messages before nudging a handoff |
| `DEMO_HANDOFF_DEADLINE` | `3` | Further messages, Architect still absent, before we add it |
| `DEMO_HARD_CAP` | `24` | Total agent messages before force-kill (pre-verdict only) |
| `DEMO_WALL_CLOCK_S` | `900` | Seconds to reach a verdict before force-kill |
| `DEMO_GRACE_S` | `20` | Post-verdict wait before closing (headless only) |
| `DEMO_OPEN_FLOOR_IDLE_S` | `420` | Presenter silence that ends an open floor |
| `DEMO_INTERACTIVE` | `true` | Open the floor after the verdict (set by `up`; `DEMO_HEADLESS=1` flips it) |

## Expected output

1. `build` produces `band-python-kit:local` and `band-python-kit-cli:local` and
   loads both as sbx templates.
2. `up` registers Maya/Sam/Jordan, grants global egress, then for each agent:
   injects the LLM + Band credentials host-side, creates the sandbox, and streams
   its **setup log to a labeled pane**.
3. The conductor creates the room and posts the brief. Maya and Sam discuss;
   Maya invites Jordan; Jordan posts a `VERDICT:`. The conductor then opens the
   floor to you (see [Ending the meeting](#ending-the-meeting)).
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
- **Open floor** (interactive, the default) — a `VERDICT:` does **not** close the
  meeting; the conductor hands you the room (see [Ending the meeting](#ending-the-meeting)
  for how to close it).
- **Clean end** (headless) — verdict + `DEMO_GRACE_S`, then close, so automation
  never waits on a presenter.

A decided meeting is never hard-killed. Only **agent** messages move the caps — your
interjections never trip the breaker. Timers run on the conductor's clock (not platform
timestamps). All caps are env-tunable (see [Tuning](#tuning)).

## Reset / re-run

`./launch.sh down` removes only what the last run recorded (sandboxes, scoped
secrets, global egress rules, agents) — never an unrelated `band-demo-*` — and
reports any resource it couldn't remove. Teardown also runs on `up` exit, so a
normal run leaves a clean host. `build` is only needed once or after a kit change.

## Notes

- **codex transport.** The Developer's codex CLI reaches `api.openai.com` over an
  HTTPS fallback because its WebSocket transport is blocked by the sandbox's MITM
  proxy. This is functional but produces retry noise in that agent's log.

## Testing

The breaker and the conductor's message projections are unit-tested offline (no
platform, no network):

```bash
uv run pytest tests/example_agents/test_docker_demo_breaker.py \
              tests/example_agents/test_docker_demo_conductor.py -v --no-cov
```
