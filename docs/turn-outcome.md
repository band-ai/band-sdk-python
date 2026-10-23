# Turn outcome

Every message pushed to an agent @mentions it, so every turn owes the room an
answer: a reply, or a deliberate decision not to reply.
`SimpleAdapter.run_judged_turn`, which `on_event` and the Slack adapter's
inbound path both call, judges each turn with one rule from `band-sdk-core`,
shared with the TypeScript SDK, and tells the platform honestly whether the
delivery succeeded.

## The rule

A turn is **complete** when it:

- replied (`band_send_message`, `band_send_room_file`)
- declined on purpose (`band_no_reply`)
- did real work (any Band tool whose effect is `act`, such as
  `band_add_participant`)
- was settled by the adapter itself (such as a control reply)
- already reported a failure (`send_failure`)

Anything else is a **missing reply**. Fetching state or narrating through
`band_send_event` only observes, so it never completes a turn. A missing reply
is reported once, as an `error` event from provider `band-runtime` carrying
core's text, and the delivery is marked FAILED. There is no nudge and no
automatic retry: the SDK reports what happened instead of claiming success.

Every Band tool call records its effect on the turn's ledger, `tools.turn`,
whichever path made the call:

```python
import asyncio

from band.testing.fake_tools import FakeAgentTools


async def main() -> None:
    tools = FakeAgentTools()

    await tools.send_event("Looking that up", message_type="thought")
    assert not tools.turn.complete  # narration only observes

    await tools.no_reply("FYI only")
    assert tools.turn.complete
    assert tools.turn.replied  # the model's final text is not relayed


asyncio.run(main())
```

A turn that ends without completing is reported, then raised as
`TurnResultAlreadyReported` so the runtime marks the delivery FAILED without a
second report:

```python
import asyncio
from datetime import UTC, datetime

import band_sdk_core

from band.core.protocols import TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing.fake_tools import FakeAgentTools, reported_failures


class Forgetful(SimpleAdapter[HistoryProvider]):
    async def on_message(
        self,
        msg,
        tools,
        history,
        participants_msg,
        contacts_msg,
        *,
        is_session_bootstrap,
        room_id,
    ):
        await tools.get_participants()


async def main() -> None:
    tools = FakeAgentTools()
    msg = PlatformMessage(
        id="m1",
        room_id="r1",
        content="@bot hi",
        sender_id="u1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )
    inp = AgentInput(
        msg=msg,
        tools=tools,
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="r1",
    )
    try:
        await Forgetful().on_event(inp)
    except TurnResultAlreadyReported:
        pass
    else:
        raise AssertionError("a turn that did nothing must be reported")

    assert [f["message"] for f in reported_failures(tools)] == [
        band_sdk_core.missing_reply_message()
    ]


asyncio.run(main())
```

## Input that was not accepted

`InputNotAccepted` means this particular attempt provably started no provider
execution. A local occupancy check before sending or a validated provider busy
rejection can establish that; timeouts and transport errors cannot. The SDK does
not judge, fail or complete the rejected delivery, and does not spend its
execution-failure retry budget. It remains outstanding for the room scheduler.
One-shot invocation instead returns `OneShotStatus.NOT_ACCEPTED`; its caller
owns later invocation, with no acknowledgement or history drain.

Ordinary pending work keeps its order. Adapter-owned approval, status and
question controls remain reachable behind it, using the existing authorization
rules and the control message's own tools. A consumed control completes only
itself. Already accepted detached work retains its original tools until its
actual end and follows the existing acknowledgement and judgment rules.

The default preprocessor prepares bootstrap, participant and system context
without consuming it. A rejected attempt leaves that context owed. History
conversion and NOOA setup also retain it when they fail or are cancelled before
context handoff. All other outcomes commit conservatively, preserving updates
that arrived during the attempt. Custom preprocessors that consume context must
defer that consumption through `AgentInput.commit_context` to support this contract. This waiting is
process-local recovery, not an exactly-once guarantee across crashes.

## Setup and context consumption

`band.core.handoff.context_handoff` distinguishes context consumption from turn
outcome. Known setup runs inside its guard and calls `handoff.consume()`
immediately before framework execution. An abnormal exit before consumption
leaves prepared context owed to the next delivery; the original exception and
cancellation propagate unchanged. Ordinary setup errors still spend the normal
retry budget and produce failed acknowledgements.

`Agent` scopes this decision to one delivery. Nested deliveries restore their
caller's decision, and detached tasks cannot change the parent decision. Once
framework execution is entered, subsequent failures cannot undo consumption.
Adapters without an explicit handoff keep conservative commitment. The shared
history conversion path adopts this contract; NOOA also adopts it for room-agent
construction, transcript installation, prompt preparation, observation and tool
binding. The preprocessor remains the sole owner of pending context.

## Declining with `band_no_reply`

`band_no_reply` is in every adapter's tool set, and `BASE_INSTRUCTIONS` tells
the model to call it when the latest message needs no answer: it was addressed
to someone else, it is an FYI or an acknowledgement, or someone already answered
it. The call is local only; its `reason` goes to the agent log.

Pydantic AI ends its native model loop after a successful current-run decline
whose other tool results in that response also succeeded. Tool results, history
and usage finish through the normal run lifecycle. A failed decline, or a failed
tool in the same batch, leaves the loop running so the model can continue.
Central turn judgment remains in `SimpleAdapter`.

## Custom tools: `@declares_turn_effect`

An undeclared custom tool counts as `observe`: the SDK cannot tell a lookup from
an action, so the turn still owes a reply. A tool that completes the turn says
so:

```python
import asyncio

from pydantic import BaseModel

from band.runtime.custom_tools import declares_turn_effect, execute_custom_tool
from band.runtime.tools import TurnEffect
from band.testing.fake_tools import FakeAgentTools


class FileTicketInput(BaseModel):
    """File a support ticket."""

    title: str


@declares_turn_effect(TurnEffect.ACT)
async def file_ticket(args: FileTicketInput) -> str:
    return f"filed: {args.title}"


async def main() -> None:
    tools = FakeAgentTools()
    await execute_custom_tool(
        (FileTicketInput, file_ticket), {"title": "Login broken"}, turn=tools.turn
    )
    assert tools.turn.complete


asyncio.run(main())
```

`REPLY` declares a tool that delivers the answer itself, and `DECLINE` one
whose silence is the answer; both also stop a relaying adapter from posting the
model's final text.

A declared effect is recorded only on success. A handler that raises or returns
an explicit failure (`{"ok": False}`, or a string starting with `Error:` or
`Error executing `, case-insensitively) records nothing: the turn still owes an
answer, and a failed reply or decline does not suppress the final-text relay.
Other return values, including `None` from a side-effect-only handler, count as
successful.

**Limits:** a custom tool only records on the turn it is handed. Where a tool
runs with `turn=None` (not bound to a room, as in a standalone MCP server), it
cannot complete any turn. A framework-native tool (rather than an
`(InputModel, handler)` pair) records its declared effect only where the
adapter observes it: Strands and Pydantic AI do; Agno tools and ready-made
LangChain tools count as `observe`.

## Adapter posts: `send_notice` and `settle()`

A post through `send_message` or `deliver_reply` counts as the turn's reply. An
adapter's own message (an approval prompt, a busy notice, a `/status` reply)
goes through `tools.send_notice`, which posts the same way but records nothing,
so it can never stand in for the model's answer. When the adapter consumes a
message without running the model, or ends the turn with its own text, it calls
`tools.turn.settle()`:

```python
import asyncio

from band.testing.fake_tools import FakeAgentTools


async def main() -> None:
    tools = FakeAgentTools()

    await tools.send_notice(
        "Still working on the previous request.", mentions=["@alice"]
    )
    assert not tools.turn.complete  # a notice is never the reply

    tools.turn.settle()
    assert tools.turn.complete


asyncio.run(main())
```

`relay_reply(tools, text, mentions)` from `band.core.delivery` is the one gate
for a model's final text: it posts through `deliver_reply` only when no tool
replied or declined this turn. It is a fallback, not a channel to steer
toward: the base prompt still says plain text is never delivered, because
without that line models more often send their closing narration as a second
`band_send_message` (measured live on gemini-2.5-flash).

### ACP and Codex native text

ACP clients (including OMP, Copilot and Cursor) and Codex use Band tools for
room replies and deliberate declines. Native assistant text is optional thought
telemetry without mentions, gated by `Emit.THOUGHTS`. It never settles a turn:
native-only output and failed tools followed by narration reach the existing
missing-reply verdict even with thoughts disabled. Successful reply/decline
suppresses redundant closing text; qualifying successful work still completes.
Other relaying adapters retain their native-text fallback.

Remove the retired `assistant_text_mode` config key from ACP and Codex and
`fallback_send_agent_text` from Codex. Explicit configs reject these as unknown
fields. Remove `CODEX_ASSISTANT_TEXT_MODE` and `CODEX_FALLBACK_SEND_AGENT_TEXT`
from the environment; they are no longer read. OpenCode's separate fallback
setting remains supported.

Parlant joins the non-preamble final segments from one event batch before
relaying, so recording the fallback reply cannot suppress a later segment of
that same answer.

For external Letta MCP servers, each successful grouped tool return is matched
to its call by `tool_call_id` and records that core tool's turn effect. Failed
returns record nothing; self-hosted tools record their own effects.

`tests/framework_conformance/test_reply_boundary.py` pins every `send_message`,
`deliver_reply` and `relay_reply` call outside the tool
implementations, per file with the reason it carries the model's words, so a
new one fails until it is justified.

## Failed runs returned as data: `ProviderRunError`

Some frameworks return a failed model run as a value instead of raising: Agno
sets an error status, Google ADK yields an event with `error_code`, and a Gemini
response carries a safety `finish_reason` or a blocked prompt. The judge cannot
see such a failure when the turn already did work or replied, so the adapter
raises `ProviderRunError(code, detail)` from `band.core.exceptions` inside its
turn. Its failure path reports `generic_provider_failure(provider, error)` from
`band.core.protocols`: the generic message plus the coarse `code` (such as
`SAFETY`). The provider's `detail` can echo the prompt, so it goes only to the
agent log. Agno, Google ADK and Gemini follow this rule.

## Detached turns

An adapter whose turn outlives `on_message` (one parked on a human approval)
calls `tools.turn.detach()` when it releases the turn early. `run_judged_turn` then
leaves the verdict to the adapter, which reports at the turn's normal end. Such a
turn starts through `start_releasable_turn` from `band.core.turn_lifecycle`: if its
room's leave or shutdown cancels it after the settle grace, it is judged then, since
its message was already handed back. An interrupt that cancels it reports nothing:

```python
import asyncio

from band.core.turn import judge_detached_turn
from band.testing.fake_tools import FakeAgentTools, reported_failures


async def main() -> None:
    tools = FakeAgentTools()
    tools.turn.judged = True  # set by SimpleAdapter.run_judged_turn
    tools.turn.detach()  # released while waiting for an approval

    # ...later, on the turn's normal completion path (never a ``finally``):
    await judge_detached_turn(tools, room_id="room-1")

    assert len(reported_failures(tools)) == 1


asyncio.run(main())
```

## Exempt adapters

`SimpleAdapter.judges_turns` is `False` where the turn is not the model's to
answer through Band tools: `A2AAdapter`, `A2AGatewayAdapter`,
`CrewAIFlowAdapter`, `BandACPServerAdapter`, `ParlantAdapter` (its engine owns
its replies) and a LangGraph adapter built from a static `graph=` (it never
gets Band tools). Synthetic contact-hub turns are never judged.

## Platform execution refusals

Recovery selects work through `/next`, including unfinished processing messages.
A refused processing mark never invokes the adapter. Stopped message/event writes
abort their originating execution scope, prevent further posts from that scope,
and leave unfinished work actionable. The refusal does not permanently pause the
room: later `/next` reconciliation can recover after PLAY without its push signal.
Scope identity and control revision prevent delayed responses from cancelling
newer work or invoking an outdated adapter interrupt hook.

Hosts that merge additional messages into a turn use
`await ctx.claim_message(message_id)` from directly awaited preprocessing in the
active handler. It returns `True` for acceptance and `False` for an ordinary
failed claim. A stopped refusal raises native `asyncio.CancelledError`, aborting
the unfinished turn through its existing scope. Calls from standalone,
background or detached tasks raise `RuntimeError` before making a request.
The host remains responsible for auxiliary local ownership and acknowledgements;
its `finally` cleanup must release uncommitted claims. SDK delivery admission
remains separate because it runs before the handler and owns the trigger's retry
accounting.

Once the handler completes, its delivery is `ACK_PENDING` until the platform
accepts completion. A stopped refusal retains that marker and retries only the
acknowledgement, without replaying handler effects or charging the acknowledgement
retry budget. This protects the running process; it does not provide exactly-once
side effects across process restarts. Ordinary acknowledgement failures retain
the existing retry policy.

One-shot invocations also require accepted claims and completion. They raise on
refusal instead of returning `DONE`; a drain records only accepted messages.

## Admission

`ExecutionContext` selects and schedules originals. Its `DeliveryLifecycle`
coordinates ordinary and control attempts through the same admission order:

1. Claim every original's ID locally. A conflict releases this attempt's claims
   and defers it.
2. Prepare context and recheck delivery eligibility. Core's `ClaimRegistry`
   owns duplicate, pending-write, ownership and retry decisions; an original
   already reported processed is skipped. Ordinary work provisionally charges
   one execution attempt per original.
3. Mark each original processing, in order, under one control revision, and
   recheck ownership after every await. Any refusal, error, cancellation or newer
   revision defers the whole selection and refunds its provisional charges:
   no model call, terminal write or execution attempt is consumed.
4. Only after the last acceptance, synchronously admit the scope, refresh
   context if needed and invoke the handler once. A control
   is handled individually without charging ordinary model work.

INTERRUPT before step 4 consumes nothing, even if the platform already accepted
a delayed mark; a late response cannot revive that scope. After step 4 an
accepted INTERRUPT consumes every original, even before the handler starts.
STOP and shutdown leave originals recoverable at every point.

## Terminal intents and settlement

At the synchronous point where a handler has finished or been stopped, before any
cleanup, activity-report or acknowledgement await, the scope fixes the one write
each of its originals owes and stores it in the runtime's `ClaimRegistry`
(`band-sdk-core`), keyed by room and original ID. The first stored intent wins;
storing another for the same original changes nothing.

| What ended the scope | Stored intent for every original |
| --- | --- |
| Handler returned, including a detached early release | `processed` |
| Accepted INTERRUPT after admission | `processed` |
| STOP, shutdown, `InputNotAccepted`, or a stopped handler | none; the originals stay recoverable |
| Handler raised, or `max_cycle_seconds` expired | `failed` with the generic provider-failure message (never exception text), plus its post-confirmation continuation |

A failed turn is reported to the room once, when `report_turn_failures_to_room`
is on and the adapter has not already reported it. Once an intent is
stored, a later STOP or refusal only defers its write: it never turns `processed`
into `failed` and never reruns the model.

With execution retries left, a FAILED attempt retains its charged count and a
retry continuation alongside the write. The SDK retries only that write while its
result is unknown. Confirmation restores the charge into the current execution's
tracker and permits the next configured attempt; it does not mark the input
completed. Exhaustion instead finishes the input. Context replacement preserves
this continuation, including when ordinary retry history was evicted while the
write was pending.

The write goes through `mark_processed_result` or `mark_failed_result` and is
classified by [`SettlementResult`](rest-client.md#lifecycle-acceptance):

| Result | Effect on the stored intent |
| --- | --- |
| `ACCEPTED` | Apply the stored continuation once: finish the input, or permit its next configured attempt with prior failures still charged. |
| `UNCONFIRMED` | Retained and retried on the reconciliation cadence, at most once per interval. Exhausting the retry count only logs a warning; the intent is never dropped, because dropping it would replay the handler. |
| `NOT_FOUND` with the room still readable | Retired as inferred missing: its intent is dropped locally and its original released. Nothing is written and nothing re-executes it. |
| `NOT_FOUND` with the room unreadable, `REJECTED` | Parked: kept, but neither retried nor blocking other inputs. A reconnect, or a room start (including a re-join), unparks it for one more try. Nothing completes it and nothing re-executes it. |
| HTTP 204 | `RoomExecutionStoppedError`: the intent is retained and retried later, uncharged. |

`NOT_FOUND` alone is not a diagnosis: the platform returns the same 404 for a
missing or soft-deleted message and for lost room access. After a 404 the SDK
reads `/next` once. A successful read observes current room access, and the SDK
infers deletion. The write and probe are not atomic: if access is removed and
restored between them, the SDK can retire an original that still exists. Local
retirement is neither proof of deletion nor successful terminal settlement.
A failed read leaves the cause ambiguous, and the intent parks. A processing
claim answered 404 follows the same rule: an inferred missing original leaves
the retained line, and one in an unreadable room is retried later.

Every recovery entrance (the reconciliation pass, the control probe, `/next`
backlog and a live duplicate delivery) retries through one helper that reads the
stored intent: `processed` writes `mark_processed_result`, `failed` writes
`mark_failed_result` with the stored error. None of them reopens processing or
reaches the handler.

Each settlement request carries a local obligation token. A stale response cannot
clear a newer obligation, and a retired execution cannot restore retry state into
its obsolete tracker. These are in-process ownership checks; the platform does
not provide an exclusive processing lease or conditional terminal writes.

### What a settled turn leaves for the scheduler

Three rules follow from the stored state alone, whatever ended the turn:

- **Outcome.** An admitted turn with no stored intent leaves its originals
  recoverable. One with an intent has advanced only when none of its originals
  still owes a write the platform has neither accepted nor refused; otherwise it
  retries later. Success, failure and INTERRUPT follow the same rule.
- **Ordinary input.** While any write in the room is owed, ordinary input waits.
  The scheduler reads this on every step, so a write that becomes owed mid-pass
  (a reconnect unparking it) holds the next input too. Parked writes do not
  block, and control discovery keeps running.
- **Retained line.** An original leaves the retained line when its turn
  advanced, or when no turn will run it and it is not kept for a pending retry.
  A roster event leaves once it has begun, since its update and callback already
  ran; one the room could not begin waits for the next wake.

## Cancellation ownership

Each runtime handler and SDK-managed Claude, Codex or OpenCode worker has one task owner.
The originating turn scope coordinates termination and fixes one absolute cleanup
deadline using the existing one-second grace. Repeated controls still notify the
adapter and update the unsettled delivery outcome, but never cancel its cleanup
again or renew its deadline.

A stopped tool or auxiliary claim first revokes that scope's posting permission
and raises cancellation in its caller. Bound workers and the handler get the same
cleanup grace to unwind cooperatively; an unfinished task receives cancellation
once at expiry if none was delivered, even when nothing waits for it. Native
STOP and INTERRUPT controls, heartbeat refusal and watchdog expiry cancel the
originating turn, including a worker released for approval. Leave and shutdown
clear reporting while released worker cleanup remains with adapter residency. Provider execution and recovery timeouts retain their adapter
policy and coordinate with that same owner.

Unfinished tasks remain owned after grace expires. The stop attempt that first
retains a task does not cancel it again or renew its grace. Retained work cannot
post late replies, change a newer turn's activity, or overlap replacement provider
work. Room cleanup reports `CleanupIncompleteError` while work remains unfinished,
and the host keeps dependent adapter resources open.

A later explicit host cleanup retry may cancel previously retained work again
within a fresh one-second grace, so a task that swallowed its first cancellation
can still settle. Once work settles, a cleanup retry completes retirement. Worker
completion alone does not readmit the room. Successful approval release retains
its original tools and judgment until the worker actually finishes;
`Turn.detach()` is still only an outcome-ledger operation.

Stopping the work an external backend runs for the turn is the adapter's half,
owned by one `BackendTurns` (`band.core.turn_lifecycle`) per adapter. The task
that owns the backend call holds `running(room_id)` around it, outside any
timeout scope, and binds the handle the backend reveals; when the call that
starts the turn reveals it, `started(room_id, call)` lets that call finish after
a stop so the accepted turn is still stopped. The adapter supplies only the
cancel call for that handle. A cancelled turn, `on_interrupt` (STOP or
INTERRUPT, including for a detached turn), the watchdog and shutdown all stop it
once, in a task the primitive owns, bounded by `BACKEND_CANCEL_TIMEOUT_SECONDS`
and never awaited on the control-processing task. A timeout branch awaits
`stop` before its own cleanup and report. The room's next
turn waits for `settled(room_id)`, and teardown for `settled` or `drain()`,
before reacquiring or closing the backend.

## Working-state lifetime

The activity owner is the originating turn scope, independently of the current
message delivery. A registered provider worker keeps its reporter alive after
the handler settles, including while waiting for human approval. Later approval
replies and busy deliveries do not replace that owner or restart its duration
cap. Reporting ends when both the handler and its workers finish. Only a
message handler's turn reports; a worker bound by a housekeeping turn, such as
a participant change, never starts the indicator.

An echoed heartbeat STOP can cancel that detached provider and notify its adapter
once. It does not rewrite the original delivery's settled acknowledgement.
Explicit interruption invalidates its activity; leave and shutdown clear the
indicator while adapter residency retains responsibility for released workers.
The authoritative idle report finishes before a replacement reports working.

`SessionConfig.enable_working_state` controls both the indicator and activity
detail, independently of chat narration and tool capabilities. Each turn keeps
its own detail, starting at reasoning; the room shows the owner's detail. An
aborted owner's stale detail is never sent: its reports while it unwinds carry
no detail, which the platform shows as reasoning. Reporting what a turn is doing
never starts reporting, changes ownership, or waits on the network: a visible
change wakes the reporter's keep-alive, which sends the latest detail, so
changes made while a report is in flight coalesce. The duration cap stops both kinds of refresh
without cancelling provider work; actual completion still clears.

Every Band tool call shows as a `tool_call` with its name, or `memory` while it
saves a memory, then returns to reasoning when it ends, even on failure or
cancellation. `band.runtime.toolactivity` owns that mapping: wrap any other tool
call in its `tool_activity`. Only the outermost call reports, so a Band tool
that calls other Band tools shows as itself throughout. `send_event` reports
only where a model invoked it, since adapters also post their own events
through it. A tool's end lands on the turn it started on, even if its tools are
rebound to a newer turn meanwhile, and a failed report never changes the tool's
outcome.

Detail compatibility is learned from the `X-API-Version` header the platform
puts on every activity response. Reports stay boolean until a response in the
process advertises agent API 1.25.0 or later; a missing or older version keeps
boolean reporting. A detail-bearing HTTP 422 permanently disables detail across
all links in the process and retries that report once without detail, within
its original deadline.
