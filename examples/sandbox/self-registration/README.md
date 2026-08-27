# Self-registration demo

Runs INT-982's self-registration flow end to end against a real Band
deployment: no pre-provisioned agent, no manually copied id/key. One script
proves the whole chain — `band-kit provision` registers a fresh agent on the
host, boots it into a real Docker Sandbox, a room message round-trips through
it, and a repeat `provision` call proves it never duplicates the
registration. Everything it creates is torn down again before it exits.

This is a dev/validation tool, not a customer-facing example — like
[`../staging-smoke/`](../staging-smoke/), it drives a real `sbx` sandbox and
needs a nested-virtualization-capable host, so it isn't run in CI.

## Prerequisites

- [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) (`sbx`) installed
  and signed in: `sbx login`
- The `band-python-kit` image available to the sandbox runtime — either a
  published tag, or built and loaded locally (see the [kit README's
  "Developing the kit"](../../../docker/band_python_kit/README.md#developing-the-kit))
- `.env.test` at the repo root with `BAND_API_KEY_USER` (plus
  `BAND_REST_URL`/`BAND_WS_URL` for a non-production deployment) — the same
  convention every other E2E/live tool in this repo uses
- This repo's dev venv (`uv sync --extra dev`) — `demo.py` reuses the E2E
  baseline toolkit, which isn't part of the published `band-sdk` package

## Run it

```bash
# Against the local kit checkout (default):
uv run examples/sandbox/self-registration/demo.py

# Against a published kit tag instead:
uv run examples/sandbox/self-registration/demo.py \
  --kit docker.io/bandhq/band-python-kit:<X.Y.Z>
```

Prints each step as it happens, then `Success: agent <id> ...` on completion.
Cleanup (sandbox, injected secret, room, agent) runs automatically whether
the run succeeds or fails.

## Recovering from an interrupted run

The automatic cleanup is a `try`/`finally` — it doesn't run if the process is
killed outright (SIGKILL, host crash). That leaves an orphaned
sandbox/secret/agent behind under the random name the run printed
(`Run name: band-selfreg-demo-...`). Recover with:

```bash
uv run examples/sandbox/self-registration/demo.py --cleanup band-selfreg-demo-<the-name>
```

This removes the sandbox, its scoped secret, and any agent registered under
that run's display name. It cannot recover the room (nothing to search it by)
— that's a harmless orphan with no plan-cap cost; delete it from the Band UI
if it matters.
