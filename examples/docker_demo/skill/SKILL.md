---
name: docker-demo
description: >-
  Run the Band × Docker three-agent design-meeting demo. Spins up a Product
  Manager (Claude SDK), a Lead Developer (Codex), and a Software Architect
  (CrewAI) — each in its own Docker sandbox, each a different framework — in one
  Band room. They design a URL shortener; the PM invites the architect for a
  decision; a host-side circuit breaker guarantees the meeting ends. Secrets
  never enter a VM. Use when presenting or rehearsing the marketing demo, or when
  asked to "run the docker demo", "start the three-agent demo", "show the
  never-in-VM demo".
when_to_use: >-
  Presenting or rehearsing the Band Docker kit demo for the Docker partnership,
  or validating the end-to-end three-agent flow from the marketplace kit.
---

# Band × Docker three-agent demo

A live design meeting: three AI agents, three frameworks, three Docker sandboxes,
one Band room. The story is **credential custody** — every agent's keys stay on
the host and are injected on the wire, so they are never present inside any VM.

## What the audience sees

1. Three sandboxes come up; each agent's **setup log streams to a labeled pane**
   (`[pm] … [dev] … [architect] …`) — the kit launcher resolving config, syncing
   the framework venv, then handing off to the agent.
2. In the Band room, **Maya (PM)** and **Sam (Dev)** discuss the URL-shortener
   design in their own voices.
3. Maya invites **Jordan (Architect)**, who returns one clear decision.
4. The meeting closes cleanly. You (a human participant) can chime in any time by
   @mentioning an agent.

## Run it

From `examples/docker_demo/`:

```bash
export BAND_API_KEY_USER=band_u_...     # you: conductor + presenter identity
export ANTHROPIC_API_KEY=sk-ant-...     # PM
export OPENAI_API_KEY=sk-...            # Dev + Architect

./launch.sh build     # once (builds + loads the two kit images)
./launch.sh up        # provision, create sandboxes, run the meeting
./launch.sh down      # tear down (also runs on exit)
```

To point at staging/self-hosted: set `BAND_REST_URL` / `BAND_WS_URL` (and
`BAND_SECRET_HOST` for the proxy wildcard) before `up`.

## The never-in-VM proof (show this)

While a sandbox is up:

```bash
sbx secret ls band-demo-pm                     # the host holds the key
sbx exec band-demo-pm env | grep -i BAND_API_KEY   # inside the VM: only "proxy-managed"
```

The VM sees the sentinel; the proxy swaps the real key on outbound `**.band.ai`
requests. The kit launcher also **fails the launch** if a real Band key is ever
found inside the VM under proxy-managed custody.

## Circuit breaker (why it can't run forever)

A host-side conductor watches the room and enforces caps, all env-tunable:

| Var | Default | Meaning |
|---|---|---|
| `DEMO_SOFT_CAP` | 6 | PM↔Dev messages before nudging a handoff |
| `DEMO_HARD_CAP` | 12 | total agent messages before force-stop |
| `DEMO_WALL_CLOCK_S` | 300 | absolute time ceiling |
| `DEMO_GRACE_S` | 20 | wait after the architect's decision, then stop |

Your own messages never count toward the caps. See `../README.md` for the full
runbook, the rehearsal-gated confirmations, and troubleshooting.
