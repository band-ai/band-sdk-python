
# INT-829 — Remote agents honor interrupt / stop / play control signals (Python SDK)

> **Status: FINAL — architect-reviewed, full consensus reached over Band.**
> SDK side only. Signal emission, dispatch gating, and stopped-flag persistence
> are platform-side (Eric's Ticket 01 / PLT-944). TDD: write tests first.

## 1. Platform contract we build against (PLT-944 — fixed, not ours to change)

- **Channel:** `agent_control:{agent_id}` (server→SDK; the SDK already joins it today for `supersede`).
- **Event:** `"agent.control"`, best-effort push.
- **Payload:**
  ```json
  {
    "type": "agent.control",
    "mode": "interrupt|stop|play",
    "scope": "agent|room",
    "agent_id": "<uuid>",
    "execution_id": "<uuid|null>",
    "room_id": "<uuid|null>",
    "reason": "user_requested",
    "correlation_id": "ctl-<base64url>"
  }
  ```
  `room_id` null = agent-scoped (fan-out across that agent's rooms). Server does **not** dedup → SDK dedups on `correlation_id`.
- **Semantics:**
  - **interrupt** (transient): abort in-flight turn, drop response, back to listening.
  - **stop** (durable): interrupt + stay silent. **Trigger suppression is platform-authoritative** — GET `/next`→204, POST mark→204, POST reply→403; stopped flag persists across reconnect. SDK need not persist stop state. WS push lands ~37–111ms *after* the platform set the durable flag.
  - **play**: explicit resume; platform clears the gate and **replays queued triggers oldest-first** → SDK catches up rehydration-style via `/next`.
- **Replay dependency (regression-guard this):** stop→play replay works because `/next` (`Chat.get_next_actionable_message`) **excludes only `processed`** — a message left in `processing` is re-returned. If the platform ever changes `/next` to also exclude `processing`, stop→play silently drops the message. Note + assert this.

## 2. Current SDK architecture (verified)

- `agent_control:{agent_id}` joined in `BandLink.connect()` → `WebSocketClient.join_agent_control_channel(...)`, today only wiring `supersede` (`streaming/client.py:402`, `platform/link.py:146`).
- WS events parsed vs `_PAYLOAD_MODELS` then dispatched per-event in `WebSocketClient._handle_events` (`client.py:341`). The channel `message_handler` runs in the **PHX receive task**, separate from per-room execution loops — the natural preemption seam.
- Per-room reasoning runs in `ExecutionContext._process_loop` (`execution.py:855`); each message handled by `await self._on_execute(self, event)` **inline** in `_process_event` (`execution.py:1467`, `mark_processed` at `:1471`, `except Exception` at `:1487`, loop `except CancelledError` at `:914`) and `_process_backlog_message` (`execution.py:1269`, `mark_processed` at `:1272`, `except Exception` at `:1288`).
- Existing cancel primitive `ExecutionContext.stop(timeout)` cancels the **whole** loop task (`execution.py:463`) — too coarse for per-cycle interrupt.
- `request_resync()` (`execution.py:526`) already drives a `/next` catch-up — reuse for **play**.
- Rooms→executions owned by `AgentRuntime.executions`; wired by `PlatformRuntime`. `request_resync` degrades via `hasattr` for custom executions (`runtime.py:235`).

## 3. Design (consensus)

### 3a. Ingestion (WS layer)
- Add `AgentControlPayload(BaseModel, extra="allow")`: `mode`/`scope` as `Literal`s, `agent_id` required, `execution_id`/`room_id`/`reason`/`correlation_id` optional.
- Register `"agent.control": AgentControlPayload` in `_PAYLOAD_MODELS`.
- Add `on_control` callback param to `join_agent_control_channel`; handler dict → `{"supersede": on_supersede, "agent.control": on_control}`.

### 3b. Routing — preemptive, out-of-band (Q-route: APPROVED)
- **Do NOT** push control onto `BandLink._event_queue` (serialized behind message processing — would defeat preemption).
- Add `BandLink.on_control` hook + `_on_control` handler, called **directly** from the receive task.
- `AgentRuntime.handle_control(payload)`:
  - Dedup on `correlation_id` (bounded LRU). Distinct signals have distinct correlation_ids, so dedup never drops a `play` that follows a `stop`.
  - Resolve targets: `scope=="agent"` & `room_id is None` → all `self.executions`; else the single room (unknown room → log + no-op).
  - Dispatch by mode → `execution.interrupt()` / `execution.stop_room()` / `execution.resume_room()`, **guarded by `hasattr`** (custom executions degrade: log + skip).
- `PlatformRuntime` sets `link.on_control = runtime.handle_control`.

### 3c. Per-cycle interrupt (Q1 APPROVED; Q2 RESOLVED; blockers a–d adopted)
- Run the cycle as a child task: `self._active_cycle_task = asyncio.create_task(self._on_execute(self, event))`, then `await` it. No platform `execution_id` matching — interrupt acts on whatever cycle is in flight for the room. If `payload.execution_id` is present, **log** it alongside `correlation_id` for platform-side debuggability.
- `interrupt(kind)` (receive-task side, **state surface = cancel + flags only**): set `self._interrupt_kind`, then `if task and not task.done(): task.cancel()`. Between-cycles (`None`/done) → no-op (**blocker c**). Do **not** touch `_processed_ids`/`self.queue` from the receive side (**blocker d**).
- asyncio cancellation drops in-flight tool `await`s (abandoned, not rolled back) — satisfies the tool-call AC for free.
- **CancelledError routing (blocker a — THE fix that makes it hold):** `CancelledError` is a `BaseException` subclass, so it bypasses the existing `except Exception`, propagates past `_process_event`/`_process_backlog_message`, and hits the loop's `except CancelledError` at `:914` → **kills the room loop permanently.** Add an explicit `except asyncio.CancelledError` **tightly around `await self._active_cycle_task`** in BOTH `_process_event` and `_process_backlog_message`:
  - if `self._interrupt_kind is not None` (interrupt/stop): swallow; handle per mode below; `return True`. **Do not re-raise.**
  - else (real shutdown cancel of the loop task propagating through): re-raise so `:914` still exits for shutdown.
- **No `asyncio.shield` needed** for the interrupt mark: only the *child* task is cancelled, so the loop task's cancel-state stays clean and a subsequent inline `await mark_processed` runs normally. (shield would only matter if the loop task itself were the cancel target = shutdown, where we don't mark anyway.)
- **Shutdown orphan (blocker b):** `ExecutionContext.stop()` cancels the loop task but **not** the awaited child → orphaned task. `stop()` must explicitly cancel (and await) `_active_cycle_task` too.

### 3d. Message status on cancel (Q2 RESOLVED)
- **interrupt** (transient): in the loop's `except CancelledError` branch → `await mark_processed` + `_remember_processed` + release claim. **Consumes** the message so the Phase-2 idle `/next` (`idle_resync_seconds`) doesn't re-return it (excludes-only-processed) and re-fire the killed cycle. Send nothing.
- **stop**: leave the message in `processing` (do **not** mark, do **not** add to `_processed_ids`), release the local in-flight claim, set `_stopped`. Platform is already stopped (push lags the durable flag), and `/next` replays the `processing` message on **play**.

### 3e. stop / play (Q3 APPROVED — minimal, efficiency-only)
- `stop_room()`: interrupt the in-flight cycle (kind=stop) + set local `_stopped=True`. `_stopped` is a **pure efficiency cache**, not an authoritative suppression decision: (1) pause Phase-2 idle `/next` polling so a stopped room isn't hammering `/next`→204; (2) short-circuit a WS trigger for that room to avoid mark→204/reply→403 churn. Nothing else. **Not persisted** across reconnect (platform gate keeps the room quiet via `/next`→204 for free).
- `resume_room()` (**play**): set `_stopped=False`, then `request_resync()` (existing) → `/next` replays the backlog (the stop-interrupted message + anything received while stopped) = rehydration-style catch-up.
- Stale `_stopped=True` only costs a quiet room until the next `play`, which always `request_resync`s — bounded divergence.

### 3f. Activity-state clearing (Q4 — seam only)
- `interrupt()` calls an optional `self._on_activity_clear` hook (default `None`/no-op). Real clearing lands with the activity-signal ticket; we only expose the seam here.

### 3g. Config (Q5 — always-on, no config)
- No `AgentControlConfig` (YAGNI). Control handling is core behavior; the channel is already joined. Add an observability hook later only if needed.

### 3h. Protocol & custom executions (Q6)
- Add `interrupt()/stop_room()/resume_room()` to the `Execution` Protocol (typed conformance, as `request_resync` was added at 0.2.0). `AgentRuntime.handle_control` `hasattr`-guards + logs-and-skips. `LettaExecution` gets no-op stubs.

## 4. Files to touch
- `src/band/client/streaming/client.py` — `AgentControlPayload` + register + `on_control` param.
- `src/band/platform/event.py` — `AgentControlEvent`/typing if needed.
- `src/band/platform/link.py` — `on_control` hook + `_on_control`.
- `src/band/runtime/execution.py` — child-task cycle; `interrupt/stop_room/resume_room`; `except CancelledError` routing in `_process_event` + `_process_backlog_message`; shutdown orphan cancel in `stop()`; `_stopped` gating in `_process_loop`; `_on_activity_clear` seam; Protocol additions.
- `src/band/runtime/runtime.py` — `handle_control` routing (dedup, target resolution, hasattr-degrade) + wiring.
- `src/band/runtime/platform_runtime.py` — set `link.on_control`.
- `src/band/integrations/.../letta` (or wherever `LettaExecution` lives) — no-op stubs.

## 5. TDD test plan (write tests first)
1. **Payload** (`tests/client/streaming/`): parse all 3 modes; null `room_id`/`execution_id`; extra fields allowed; bad `mode` rejected.
2. **Link** (`tests/platform/`): `_on_control` calls the registered hook with the parsed payload; control is **not** enqueued on `_event_queue`.
3. **Interrupt** (`tests/runtime/test_execution_interrupt.py`):
   - in-flight cycle cancelled; fake tools assert **nothing sent**; `mark_processed` + `_remember_processed` called.
   - interrupt during an awaiting tool → tool result **not** sent.
   - **REGRESSION: loop still alive** — after an interrupt, a fresh message in the same room is processed normally (guards the `CancelledError`-routing bug at `:914`).
   - interrupt between cycles (no active task) → safe no-op.
4. **Stop/Play**:
   - stop interrupts + sets `_stopped`; Phase-2 idle polling paused; message left in `processing` (not marked, not in `_processed_ids`).
   - reconnect while stopped: `/next`→204 ⇒ no processing/sends (no SDK persistence needed).
   - play clears `_stopped` + `request_resync` ⇒ backlog (incl. the stop-interrupted message) replays ⇒ cycle runs + responds.
   - **stop→play replay platform dependency**: assert/document that replay relies on `/next` excluding only `processed`.
5. **Shutdown** : `ExecutionContext.stop()` with an in-flight cycle cancels + awaits the child (no orphaned task warning); returns graceful/false correctly.
6. **Routing** (`tests/runtime/test_runtime_control.py`): agent-scope null room → all rooms; room-scope → one room; unknown room → no-op; `correlation_id` dedup; play-before-stop ordering harmless.
7. **Conformance**: custom `Execution` without the new methods degrades gracefully (log + skip).
8. Manual/E2E note: real control via platform REST (human key) — likely manual verification.

## 6. Resolved questions (consensus)
- **Q1**: interrupt current in-flight cycle; no `execution_id` matching; log it + `correlation_id`.
- **Q2**: interrupt **consumes** (`mark_processed`, inline awaited in loop except, no shield); stop **leaves `processing`** + `_stopped` for replay-on-play. Plus the explicit `except CancelledError` routing fix.
- **Q3**: minimal `_stopped` efficiency-only flag; not persisted.
- **Q4**: `_on_activity_clear` seam only.
- **Q5**: always-on; no config class.
- **Q6**: Protocol methods + `hasattr`-degrade + Letta no-op stubs.
