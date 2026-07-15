---
name: band-sandbox-staging-smoke
description: Run the Docker Sandbox staging smoke for band-sdk-python — a manual, resumable full run proving a headless agent inside a real Docker Sandbox receives a WebSocket message, replies over REST, and recovers from three interruptions (an operator Wi-Fi cycle, host sleep/wake, and a sandbox daemon restart). Use when asked to run, resume, or report on this smoke.
---

# Band Sandbox Staging Smoke

Drives the same files an operator would run by hand (`setup.sh`, `run.sh`,
`probe.py`) plus the scripts in this directory. Neither path is a second
implementation of the scenario — both read and write the same
`.sandbox-smoke/state.json` and produce the same `evidence.md`.

**This is a manual, occasionally-run example, not an automated test.** Do not
try to make it non-interactive end to end — the Wi-Fi step genuinely requires
a human, and the workflow is designed around that.

## Safety rules

- **Never print, log, or write a secret value** — not `BAND_API_KEY_USER`, not
  the sandboxed agent's own api_key, not any header. `preflight.py` and
  `probe.py` only ever report *presence* and *pass/fail*, never values.
  `state.json` never contains a credential field — see `state.py`.
- **Staging only.** `preflight.py` and `probe.py` reject the SDK's own
  production defaults outright; if either fails on that check, stop — do not
  work around it by hand-supplying a production URL.
- **Stop at the Wi-Fi checkpoint.** After the initial probe passes, end your
  active turn instead of trying to stay connected or poll for connectivity —
  turning Wi-Fi off can disconnect the operator from you as well as the
  sandbox. Resume only when the operator confirms connectivity is back.
- **On failure, keep only redacted diagnostics** — `.sandbox-smoke/agent.log`
  and `sbx policy log` output, never raw env dumps or config files.

## Workflow

Run every command from `examples/sandbox/staging-smoke/`.

### 1. Start

```bash
uv run skill/scripts/record-phase.py started
```

State the scope out loud to the operator: this proves a headless SDK agent's
WebSocket receipt + REST reply from inside a real Docker Sandbox, and its
recovery from **three** interruptions — a manual Wi-Fi cycle, a host
sleep/wake (both need the operator), and a sandbox daemon restart (you run
that one). All three are mandatory; a report missing any renders INCOMPLETE.

### 2. Preflight

```bash
uv run skill/scripts/preflight.py
```

Report each PASS/FAIL line verbatim (it already omits values). Any FAIL stops
the run here — fix the missing prerequisite (see `README.md`) before
continuing.

### 3. Sandbox creation

```bash
./setup.sh
```

This creates (or reuses) the disposable sandbox and installs the SDK-under-test
inside it. It does **not** touch the Band platform — no agent/room exists yet.
(`setup.sh` also runs `preflight.py` itself as its own first step — this is a
backstop for the plain operator workflow, which never calls step 2 above; it's
not a second, different check.)

### 4. Start the agent

Run `./run.sh` **in the background** (it provisions a fresh Band agent + room
via `probe.py --label provision`, then keeps running in the foreground to log
connect/disconnect/reconnect activity — you need to keep driving the
workflow, so launch it detached and tail its log instead of blocking on it).
Wait until `.sandbox-smoke/agent.log` shows the agent's non-secret readiness
line before continuing, and tell the operator the agent is up.

### 5. Initial probe

```bash
uv run probe.py --label initial
```

A non-zero exit means the round trip failed — report the (redacted) reason
from its output and stop; do not continue to the Wi-Fi step.

### 6. Wi-Fi checkpoint — end your turn here

```bash
uv run skill/scripts/record-phase.py awaiting-wifi-recovery
```

Print its output (the exact resume instruction) to the operator, then **stop**.
Do not wait, poll, or try to detect reconnection yourself.

### 7. Resume after Wi-Fi is restored

When the operator confirms connectivity is back (a new message, not a
continuation of the same blocked turn):

```bash
uv run skill/scripts/preflight.py         # re-check prerequisites
uv run probe.py --label after-wifi-reconnect
```

A non-zero exit from the probe means reconnect did not complete — report the
reason; the run is a FAIL, not a retry loop.

### 8. Sleep/wake checkpoint — end your turn here (mandatory)

This check is **not optional** — a report missing it renders INCOMPLETE.

```bash
uv run skill/scripts/record-phase.py awaiting-sleep-wake
```

Print its output (sleep the host ~1 minute, wake, resume), then **stop**, the
same as the Wi-Fi checkpoint — the host sleeping suspends you too.

When the operator confirms they're back: check `sbx ls` (expect the sandbox
still `running`) and the tail of `.sandbox-smoke/agent.log` (expect a
`WebSocket reconnected` line after the wake), then:

```bash
uv run probe.py --label after-sleep-wake
uv run skill/scripts/record-observation.py sleep_wake \
  "<did the VM/process survive; how did it recover>"
```

The observation is the *behavior*, not a bare pass/fail — the probe row is
the verdict; this is the explanation the report prints beside it.

### 9. Daemon restart (mandatory — you run this one)

No operator handoff needed: restarting the sandbox daemon does not disconnect
you (it only affects sandboxes). **Expect the agent to die** — the daemon stop
severs the `sbx exec` attach and stops the sandbox VM, and the freshly-minted
credentials died with the process (they are deliberately never on disk), so
recovery is a full re-provision. The `daemon-restart` phase recorded first is
what authorizes `provision` to reuse this run (and reap the dead agent/room
itself) instead of rotating to a fresh one:

```bash
uv run skill/scripts/record-phase.py daemon-restart
sbx daemon stop
sbx daemon start -d                        # -d: detached, not foreground
sbx ls                                     # expect the sandbox: stopped
./run.sh                                   # recovery: reaps the dead agent, re-provisions, relaunches
# wait for the readiness line in .sandbox-smoke/agent.log, then:
uv run probe.py --label after-daemon-restart
uv run skill/scripts/record-observation.py daemon_restart \
  "<survived? auto-resumed? what recovery took>"
```

### 10. Cleanup and report

```bash
uv run probe.py --label cleanup      # reaps the room + agent, ends the run
uv run skill/scripts/render-report.py
```

(If the run must be abandoned as failed instead, record that explicitly:
`uv run skill/scripts/record-phase.py failed` — the report then renders FAIL.)

Read back `.sandbox-smoke/evidence.md` and give the operator a concise
PASS/FAIL/INCOMPLETE summary plus the report path. Then stop the sandboxed
`run.sh` process, remove the disposable sandbox, and delete its workspace
(see `README.md`'s cleanup section) — `probe.py --label cleanup` only reaps
the Band-side room/agent, not the sandbox itself.

## Reference

`references/requirements.md` maps each requirement to the evidence this
smoke produces and the script that produces it — read it if asked to justify
what a PASS actually proves.
