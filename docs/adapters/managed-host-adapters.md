# Managed Host Adapters

What a host that embeds a harness adapter can rely on beyond each adapter guide.

## Idle room resource release

Set `SessionConfig(release_idle_room_after_s=...)` to have the runtime ask the adapter to release a room's harness resources once the room has been idle that long after a turn. The room stays joined; its next message recreates the process and continues the same conversation. The default `None` releases nothing.

The release runs on the room's own processing loop, between turns: a turn in progress is never released, a message that arrives first is processed first, and a room that never ran a turn is left alone. The next turn re-arms the timer. A failed release is logged and the room keeps serving. Leaving the room or stopping the agent needs no extra cleanup, since there is no separate timer task. Stopping the agent or leaving the room while a release is tearing a process down waits for that teardown to finish; cancelling that stop leaves the teardown running for a later stop to finish.

| Adapter | Releases | Resumes with | Verified |
|---|---|---|---|
| Codex | the room's app-server process | `thread/resume` with the room's thread id (Codex keeps threads on disk); if the resume fails, a fresh thread starts with the refetched room transcript injected, and the turn fails instead when the transcript can't be fetched | live, Codex 0.156.1: a fact from the first turn was recalled after release |
| OMP (ACP) | the room's `omp acp` process, when it advertised `session/load` | `session/load` with the room's session id; if the load fails, the existing fallback applies (new session plus transcript replay) | live, OMP 18.3.2: a fact from the first turn was recalled after release |
| Copilot CLI, Cursor, and generic ACP | nothing (no-op) | — | no cross-process recall proven here; an agent without `session/load` would also come back blank |
| Copilot SDK, OpenCode, Letta, and the in-process framework adapters | nothing (no-op) | — | no per-room harness process that could be released and resumed |

```python
from band.runtime.types import SessionConfig

config = SessionConfig(release_idle_room_after_s=900.0)
assert config.release_idle_room_after_s == 900.0
```

Hosts that restarted the agent on a schedule to reclaim idle harness processes can use this instead.
