# Idle room resource retention

Automatic idle release is **unavailable for every production adapter**. The SDK
contains a shared lifecycle foundation and explicit configuration gates; provider
integration and live suspension acceptance remain blocked by native safety
prerequisites. Existing room processes remain resident until room cleanup or
adapter shutdown.

`CodexAdapterConfig`, `ClaudeSDKAdapterConfig`, and the ACP client configs accept
a frozen `RoomRetentionConfig` under `retention`. Leaving `idle_timeout_s=None`
keeps existing behavior. An enabled timeout must be finite and strictly positive,
but every current provider rejects it during configuration validation with its
specific qualification blocker. This also applies to JSON/YAML configuration and
Codex environment settings. There is no supported bypass or restricted feature
profile.

```python
from pydantic import ValidationError

from band.adapters.codex import CodexAdapterConfig
from band.core.retention import RoomRetentionConfig

try:
    CodexAdapterConfig(retention=RoomRetentionConfig(idle_timeout_s=60))
except ValidationError as error:
    assert "idle retention is unavailable" in str(error)
else:
    raise AssertionError("Unqualified providers must reject idle retention")
```

## Why turn completion is insufficient

Room membership, native conversation state, and live provider resources have
different lifetimes. A completed Band turn does not prove that native tools,
delegated agents, hooks, scheduled prompts, or parent continuation have stopped.
Stopping a process after an inactivity timer can destroy that work even when the
same conversation subsequently reloads successfully.

Qualification on 2026-10-11 found these blockers:

| Profile examined | Evidence | Missing guarantee |
| --- | --- | --- |
| Codex CLI 0.160.0 and 0.162.1 | Native probes reproduced background terminal and asynchronous hook loss on shutdown after foreground completion. The hook case had an idle thread and empty terminal inventory. | Whole-session inactivity or a non-cancelling drain covering hooks, terminals, and delegated continuation. |
| Claude SDK 0.2.159; candidate 0.2.165 with CLI 2.1.294 | Candidate probes passed isolated shell/subagent completion and same-session resume, but asynchronous hooks continued after empty Stop inventories and native idle. Disconnect killed them; a resident control completed. | Hook-inclusive inactivity/drain or a complete effective configuration and change boundary, including managed policy. |
| Copilot ACP CLI 1.0.89 | Native probes showed scheduled `/after` work survives in a resident process but is lost after shutdown and successful reload of the same session. | Activity/drain that includes scheduled autonomous work. |
| OMP ACP 18.2.8 | Released source shows pending-work helpers omit suppressed/acknowledged jobs and shutdown cancels jobs. | Activity/drain covering every owned job. Source evidence; no native loss probe was executed for this row. |
| Cursor ACP 2026.09.28-64d2043 | Installed source drains subagent completions but does not establish general background-work inactivity. | A demonstrated whole-session safe release boundary. Source evidence; no native loss probe was executed for this row. |
| Generic ACP | `end_turn`, `loadSession`, and session closure do not provide a general non-cancelling process drain. | Qualification for the concrete provider and launch profile. |

The native probes used actual provider binaries with deterministic local model
responses. They are not live Band/LLM acceptance. These findings do not rule out
future provider contracts; upgrading a dependency alone does not qualify it.
Copilot SDK's server-wide timeout is a different ownership model and is excluded.

## Shared lifecycle contract

The common owner lives in `band.core.retention`, below the adapter boundary.
It separates full-duration SDK activity leases from provider activity, uses one
monotonic scheduler per owner, and serializes transitions within each room while
allowing independent rooms to progress. Unknown provider activity keeps a room
resident. It must never be inferred quiescent from an empty local task ledger.

Suspension must retain native conversation identity, effective settings, and the
workspace claim. A wake must resume that exact conversation; failed resume must
retain its identity for retry and must not silently replay public history into a
new session. Only final disposal releases room ownership. Failed or cancelled
cleanup must retain concrete resource ownership, and a failed release must retry
after the configured idle interval without waiting for another room message.

Accepted work enters one ready-I/O passage: `RetainedRoom.own(task)` enrolls a
detached task before its first await, and `use(prepare)` settles any earlier
transition, leases active I/O, binds the operation, then acquires or strictly
restores the room. Every physical retirement carries one `ReleaseCause`
(`IDLE`, `RECOVERY`, `FINAL`). An idle release may return `ReleaseOutcome.BUSY`
only when nothing was torn down; a raised error leaves cleanup pending with its
cause and handles retained. A crashed or abandoned residency goes through
`recover()`, which revokes its event authority and releases once the reporting
operation unwinds. `transition_timeout_s` bounds how long a caller waits to
learn an outcome; an unfinished transition stays owned and is never treated as
closed.

These ownership rules hold with retention disabled. Room, execution, adapter and
shared MCP cleanup that fails or is cancelled stays owned, with the agent's
single-instance guard, and the next `stop()` or `shutdown()` retries it instead
of reporting success.

The shared mechanics do not certify a provider's release callback. Production
integration still needs authoritative native inactivity or a demonstrated
non-cancelling drain, uninterrupted background event handling, strict resume,
tool/settings rebinding, and owned final cleanup. Shared adapter MCP resources
must survive suspension of an individual room. Until those prerequisites and the
required native/live tests pass, provider retention stays unavailable.

Provider references: [Codex app-server](https://learn.chatgpt.com/docs/app-server),
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Claude managed settings](https://code.claude.com/docs/en/settings),
[ACP session setup](https://agentclientprotocol.com/protocol/v1/session-setup).
