# Consuming queued messages in one turn

By default every delivery is its own model turn: three messages queued behind a
busy agent cost three turns. With `SessionConfig.consume_all_queued` the room
instead hands the agent the ordinary messages already waiting, oldest first, as
one turn with one outcome. It is off unless you turn it on.

## What it does

- **Best effort.** When the room is ready for its next turn it takes what is
  available then. It never waits to fill a batch and has no debounce: one waiting
  message is a turn of one.
- **A ceiling, not a target.** `max_queued_messages` (1 to 100, default 10) caps
  how many originals one turn takes. It bounds the count, not tokens; provider
  limits still apply.
- **One room, in order.** Originals keep their sender, timestamp and metadata
  (`AgentInput.messages`). Rooms are never combined.
- **Boundaries end a batch.** The ceiling, a participant or other non-message
  event, and a delivery REST does not list (a thread reply, which runs on its own
  at its position) all end the prefix. Later arrivals never join a turn already
  chosen. See [queued-message selection](websocket-events.md#queued-message-selection).
- **All or nothing admission.** Every selected original is marked processing
  before the model runs. If any mark is refused, fails or is overtaken by a STOP,
  nothing runs, nothing is completed and no attempt is charged; the whole
  selection is retried. See [admission](turn-outcome.md#admission).
- **One model attempt and one shared outcome.** The agent is invoked once. A
  reply, decline or failure applies to every original in the turn.

## Enable it

`consume_all_queued` and `max_queued_messages` are `SessionConfig` fields, passed
as `session_config=` to `Agent.create` or `Agent.from_config`. The YAML file holds
credentials only; there is no file or environment setting for session options.

```python
import asyncio
from typing import Any

from band import Agent, SessionConfig
from band.core.exceptions import BandConfigError
from band.core.simple_adapter import SimpleAdapter

config = SessionConfig(consume_all_queued=True, max_queued_messages=25)
assert (config.consume_all_queued, config.max_queued_messages) == (True, 25)
assert SessionConfig().consume_all_queued is False  # off unless asked for

for invalid in (
    {"max_queued_messages": 0},  # the ceiling is 1..100
    {"max_queued_messages": 101},
    {"consume_all_queued": True, "max_message_retries": 2},  # one attempt per batch
):
    try:
        SessionConfig(**invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{invalid} must be rejected")


class PlainAdapter(SimpleAdapter[Any]):
    """Never declared ``supports_batch_input``."""

    async def on_message(self, *args: Any, **kwargs: Any) -> None: ...


agent = Agent.create(
    PlainAdapter(), agent_id="agent-1", api_key="key", session_config=config
)
with asyncio.Runner() as runner:
    try:
        runner.run(agent.start())
    except BandConfigError as error:
        assert "supports_batch_input" in str(error)
    else:
        raise AssertionError("an adapter without batch support must be refused")
```

Enabling it is refused when it cannot work, rather than falling back silently:

| Situation | Error |
| --- | --- |
| `max_queued_messages` outside 1 to 100 or not an `int` | `ValueError` from `SessionConfig` |
| `max_message_retries` other than 1 | `ValueError` from `SessionConfig` |
| Adapter without `supports_batch_input`, or a custom preprocessor without `process_batch` | `BandConfigError` from `Agent.start()` |
| A custom `execution_factory`, or an execution without a batch callback | `BandConfigError` at construction |
| One-shot invocation (`OneShotInvoker`) | `BandConfigError` at construction |

One-shot keeps its one-event-per-invocation drain; it never batches.

## Supported adapters

An adapter opts in by setting `supports_batch_input = True`. A `SimpleAdapter`
overrides `on_turn(messages, ...)`, its one model-input builder, and makes
`on_message` call `on_turn((msg,), ...)`, so a singleton and a batch share one
tool loop and one judge. A bare `FrameworkAdapter` implements
`on_messages(inp: AgentInput)` itself. The runtime hands a batch to
`on_messages`, and `AgentInput` carries every original; `msg` stays the first
one, so a singleton consumer keeps working:

```python
from datetime import UTC, datetime

from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing.fake_tools import FakeAgentTools


def message(message_id: str, sender_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id="room-1",
        content=f"@bot {message_id}",
        sender_id=sender_id,
        sender_type="User",
        sender_name=None,
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def agent_input(*messages: PlatformMessage) -> AgentInput:
    return AgentInput(
        msg=messages[0],
        messages=messages,
        tools=FakeAgentTools(),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )


batch = agent_input(
    message("m1", "alice"), message("m2", "bob"), message("m3", "alice")
)
assert [m.id for m in batch.messages] == ["m1", "m2", "m3"]  # arrival order
assert batch.msg is batch.messages[0]  # the real first original, not a composite
assert batch.sender_ids == ("alice", "bob")  # each distinct sender once

single = AgentInput(
    msg=message("m1", "alice"),
    tools=FakeAgentTools(),
    history=HistoryProvider(raw=[]),
    participants_msg=None,
    contacts_msg=None,
    is_session_bootstrap=False,
    room_id="room-1",
)
assert single.messages == (single.msg,)  # a turn of one is the singleton case
```

These built-in adapters declare `supports_batch_input`:

| Family | Adapters |
| --- | --- |
| Model SDKs | Anthropic, Gemini, Google ADK, LangGraph, Pydantic AI, Agno, Strands, CrewAI, Parlant, NOOA, Letta |
| Coding agents | Claude SDK, Codex, Copilot SDK, OpenCode |
| ACP clients | the generic ACP client, Copilot ACP, OMP ACP, Cursor ACP |

These do not, so enabling `consume_all_queued` with one raises `BandConfigError`
at `Agent.start()`:

| Adapter | Why |
| --- | --- |
| A2A and the A2A gateway | The remote agent or the pending A2A task owns the reply; there is no model turn to share. |
| CrewAI Flow | The Flow's run id and its reply matching are keyed to one original. |
| ACP server | Each editor prompt settles on its own peer message. |
| Slack wrapper | It forwards to an inner adapter and does not yet delegate control recognition. |

## Replies and declines

The model is told to consider every current input together. It answers with
`band_send_message`; one reply may answer several inputs. It calls
`band_no_reply` only when the turn as a whole needs no answer, and that ends the
whole turn: a decline never skips a single input. An FYI does not cancel another
input's outstanding request, and a correction or cancellation is read in context.
This is the same text singleton turns use
([declining](turn-outcome.md#declining-with-band_no_reply)). It is guidance, not
a coverage check: the SDK judges that the turn replied, declined or did work, not
that each input was answered.

## Failure, retries and recovery

- **A failed batch fails every included delivery**, with one failure report and
  no automatic replay. Effects the model already caused remain real.
- **Failed inputs are never retried automatically in batch mode**, including
  after a restart: a delivery the platform reports `failed` for the agent is
  skipped when selecting. Re-send the message to run it again. An unfinished
  `processing` delivery after a crash is still picked up, so work can repeat
  (at-least-once).
- **Terminal writes are retried, the model is not.** The processed or failed
  write each original owes is stored when the turn ends and retried alone if the
  platform does not confirm it; see
  [terminal intents and settlement](turn-outcome.md#terminal-intents-and-settlement).
  Intents are held in memory, so a process exit can lose them.
- **STOP and INTERRUPT.** An INTERRUPT before every original is admitted
  consumes nothing; after admission it consumes them all. STOP leaves them
  recoverable. With batching off, singleton turns follow the same admission
  order.

## Limitations

- **Attachments are not newly hydrated.** Batching passes each original's
  existing content and metadata and adds no attachment download or multimodal
  input. File tools are separate and stay behind `Capability.FILES`.
- **Metadata is kept, not shown to the model.** Each original's `metadata` stays
  on its own `PlatformMessage`; batching does not merge it or add it to prompts.
- **WebSocket-only originals are not durable.** A delivery REST omits is kept
  only while the room's execution holds its payload. For such backlog the
  stopped-room `/next` probe cannot tell an empty room from a missed PLAY; see
  [limits of WebSocket-only backlog](websocket-events.md#limits-of-websocket-only-backlog).
- **Long failed history is re-read.** Failed deliveries ahead of new work are
  scanned 100 per pass; nothing hides them, so a later scan can find one another
  runtime reopened.
- **Cursor runs one turn at a time.** A busy Cursor adapter returns ordinary
  input to the room's scheduler instead of batching it into the running turn; see
  [Cursor provider slot](acp.md#cursor-provider-slot).

## Approvals and controls

Approval, status and question replies are recognised through a read-only
`is_control_message(msg)` adapter hook and handled one at a time under their own
delivery and the existing authorization rules, never inside a batch. A reply
recognised as a control ends the batch before it. Recognition alone never
consumes or acknowledges a message, and every approval request keeps its own
identity.

An adapter that overrides `try_handle_control` without `is_control_message`
is treated conservatively: every message is a control, so nothing batches.
Claude SDK recognises its approval commands while approvals are on, Codex
recognises every local command, and Cursor recognises `/cursor` commands, valid
or not.

## Senders and reply audience

Each original keeps its own sender ID, name and type, and the model input
labels each current input with them. An automatic reply or notice (one the SDK
sends, not one the model addresses itself) mentions each distinct original
sender once, in first-appearance order: a batch from A, B, A addresses A and B.
Mentions the model chooses are never rewritten. Approval authorization comes
from the adapter's configured rules, never from the union of message senders.
