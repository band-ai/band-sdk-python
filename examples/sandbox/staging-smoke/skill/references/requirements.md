# Requirements and evidence mapping

Reference for the skill workflow (`SKILL.md`) and for anyone auditing a run's
`evidence.md`. Maps what this smoke needs to prove, one-to-one with what it
actually produces as evidence.

| Requirement | Evidence | Produced by |
|---|---|---|
| Headless SDK agent in a real sandbox | `run.sh` starts `agent.py` with `sbx exec` inside a real Docker Sandbox | `run.sh` |
| Staging setup, not production | Staging REST/WS URLs required; the SDK's own production defaults are rejected outright | `probe.py`'s `load_settings()` |
| Dynamic, reaped agent identity | The sandboxed agent is registered fresh each run (own id + key) via `ResourceManager.provision_agent`, never a static pre-provisioned credential; a failed provisioning attempt reaps the agent immediately rather than leaking it | `probe.py --label provision` |
| WebSocket receipt | A scripted `marker:<token>` message reaches the sandboxed agent over its real WebSocket connection | `probe.py --label initial` / `agent.py` |
| REST reply | The agent replies `sandbox-ack:<token>` via `band_send_message`; the probe observes it via `reply_capture`'s `wait_for_reply` | `agent.py` / `probe.py` |
| Reconnect after network loss | The operator manually disables/enables Wi-Fi while the agent process keeps running; a second marker/reply round trip proves the same process reconnected. Retrying after a transient failure is safe — the report reflects the latest attempt per step | `probe.py --label after-wifi-reconnect` |
| Recovery after host sleep/wake (mandatory) | The operator suspends and wakes the host; a marker/reply round trip proves recovery (observed live: VM + agent survive, SDK reconnects on wake) | `probe.py --label after-sleep-wake` + a behavior note in `residual_checks` |
| Recovery after daemon restart (mandatory) | `sbx daemon stop`/`start -d` kills the sandbox VM and agent (observed live); the documented recovery path (reap + re-provision + relaunch) is then proven with a marker/reply round trip | `probe.py --label after-daemon-restart` + a behavior note in `residual_checks` |
| Repeatable staging access | `README.md` documents setup, execution, diagnostics, and cleanup end to end | `README.md`, `setup.sh`, `run.sh` |
| Redacted evidence only | `state.json` never stores credentials; `evidence.md` is rendered only from non-secret fields | `state.py`, `skill/scripts/render-report.py` |

See the design doc for the full rationale, including why this is a manual
smoke rather than an automated E2E test, and why the probe reuses
`tests/e2e/baseline`'s toolkit instead of hand-rolling REST/WS calls.
