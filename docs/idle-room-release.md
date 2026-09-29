# Idle room resource release

Set `SessionConfig(release_idle_room_after_s=...)` to have the runtime ask the
adapter to release a room's harness resources once the room has been idle that
long after a turn. The room stays joined; its next message recreates the process
and continues the same conversation where the adapter supports it. The default
`None` releases nothing.

The release runs on the room's own processing loop, between turns: a turn in
progress is never released, a message that arrives first is processed first, and
a room that never ran a turn is left alone. The next turn re-arms the timer. A
failed release is logged and the room keeps serving. Leaving the room or stopping
the agent waits for an in-flight teardown to finish.

`SimpleAdapter.release_room_resources` is the adapter hook; the default is a
no-op. **Codex** closes the idle room's app-server and resumes the same thread
on the next turn via `thread/resume`, refetching transcript when resume fails.
**Claude SDK** stops the idle room's CLI process and resumes the same session
id on the next turn, refetching transcript when resume fails. **OMP ACP**
releases the agent process when `session/load` is advertised. Other adapters
document their behavior in their adapter guides.

```python
from band.runtime.types import SessionConfig

config = SessionConfig(release_idle_room_after_s=900.0)
assert config.release_idle_room_after_s == 900.0
```
