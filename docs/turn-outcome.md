# Turn outcome

Every message pushed to an agent @mentions it, so every turn owes the room an
answer: a reply, or a deliberate decision not to reply.
`SimpleAdapter.judged_turn` judges each turn with one rule from `band-sdk-core`,
shared with the TypeScript SDK, and tells the platform honestly whether the
delivery succeeded. A singleton reaches it through `run_judged_turn`, which
`on_event` and the Slack adapter's inbound path both call; a batch
([consuming queued messages](consume-all-queued.md)) through `on_messages`.

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


with asyncio.Runner() as runner:
    runner.run(main())
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


with asyncio.Runner() as runner:
    runner.run(main())
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
without consuming it. A rejected attempt leaves that context owed; every other
outcome commits it conservatively, preserving updates that arrived during the
attempt. Custom preprocessors that consume context must defer that consumption
through `AgentInput.commit_context` to support this contract. This waiting is
process-local recovery, not an exactly-once guarantee across crashes.

## Declining with `band_no_reply`

`band_no_reply` is in every adapter's tool set, and `BASE_INSTRUCTIONS` carries
the tool's canonical description from band-sdk-core: the model considers every
current input together and calls it only when the turn as a whole needs no
answer — the inputs were addressed to someone else, are FYIs or
acknowledgements, or someone already answered them. An FYI never cancels another
input's outstanding request, a correction or cancellation is read in context,
and one reply may answer several inputs. A decline ends the whole turn, never a
single input. The call is local only; its `reason` goes to the agent log.

One input is simply a turn of one, so the same text serves a singleton and a
[batched turn](consume-all-queued.md). The prompt carries core's description
verbatim, and `TURN_COMPLETION_GUIDANCE` speaks of the turn's inputs rather than
"the message":

```python
import band_sdk_core

from band.runtime.prompts import BASE_INSTRUCTIONS

description = band_sdk_core.no_reply_tool()["description"]
assert "every current input message together" in description
assert description in BASE_INSTRUCTIONS
```

This is guidance, not a coverage check: the judge still sees only the effects
above (a reply, a decline or real work), never whether each input was answered.

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


with asyncio.Runner() as runner:
    runner.run(main())
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


with asyncio.Runner() as runner:
    runner.run(main())
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
    tools.turn.judged = True  # set by SimpleAdapter.judged_turn
    tools.turn.detach()  # released while waiting for an approval

    # ...later, on the turn's normal completion path (never a ``finally``):
    await judge_detached_turn(tools, room_id="room-1")

    assert len(reported_failures(tools)) == 1


with asyncio.Runner() as runner:
    runner.run(main())
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

Once the handler completes, its stored terminal intent (see below) is written
until the platform accepts it. A stopped refusal retains the intent and retries
only that write, without replaying handler effects or charging the acknowledgement
retry budget. This protects the running process; it does not provide exactly-once
side effects across process restarts.

One-shot invocations also require accepted claims and completion. They raise on
refusal instead of returning `DONE`; a drain records only accepted messages.

## Admission

Singleton and batched attempts share one admission order, in `ExecutionContext`:

1. Claim every original's ID locally. A conflict releases this attempt's claims
   and defers it.
2. Charge one execution attempt per original (one past its retry limit is
   skipped) and hydrate context if the room has none yet. An original the
   platform already reports processed is completed and dropped.
3. Mark each original processing, in order, under one control revision, and
   recheck ownership after every await. Any refusal, error, cancellation or newer
   revision defers the whole selection and refunds the charge: no model call, no
   terminal write, no attempt left charged.
4. Only after the last acceptance, synchronously mark the scope admitted, refresh
   context if it expired and invoke the handler once.

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
| Handler raised, or `max_cycle_seconds` expired | `failed` with the generic provider-failure message (never exception text), only with `consume_all_queued`; otherwise none and the message's own retry policy applies |

A failed batch is reported to the room once, when `report_turn_failures_to_room`
is on and the adapter has not already reported it. Once an intent is
stored, a later STOP or refusal only defers its write: it never turns `processed`
into `failed` and never reruns the model.

The write goes through `mark_processed_result` or `mark_failed_result` and is
classified by [`SettlementResult`](rest-client.md#terminal-write-results):

| Result | Effect on the stored intent |
| --- | --- |
| `ACCEPTED` | The original completes locally. This means the delivery is settled, not that the model succeeded. |
| `UNCONFIRMED` | Retained and retried on the reconciliation cadence. Exhausting the retry count only logs a warning; the intent is never dropped, because dropping it would replay the handler. |
| `NOT_FOUND`, `REJECTED` | Parked: kept, but neither retried nor blocking other inputs. A reconnect, or a room start (including a re-join), unparks it for one more try. Nothing completes it and nothing re-executes it. |
| HTTP 204 | `RoomExecutionStoppedError`: the intent is retained and retried later, uncharged. |

`NOT_FOUND` is not a diagnosis: the platform returns the same 404 for a missing
or soft-deleted message and for lost room access, so the SDK parks the intent
instead of guessing. While an unconfirmed write remains, the room keeps its
ordinary work deferred and retries on the reconciliation cadence; control
discovery and other rooms keep running. A bad first write does not starve its
siblings: one pass attempts every original of the attempt.

Every recovery entrance (the reconciliation pass, the control probe, `/next`
backlog and a live duplicate delivery) retries through one helper that reads the
stored intent: `processed` writes `mark_processed_result`, `failed` writes
`mark_failed_result` with the stored error. None of them reopens processing or
reaches the handler.

The registry belongs to the runtime, so a pending intent survives replacement of
a room's execution; process exit loses it. Delivery stays at-least-once across
restarts: an original left in `processing` by a crash runs again. In batch mode a
durably `failed` original is not selected again (see
[Consuming queued messages in one turn](consume-all-queued.md)).

## Cancellation ownership

Each runtime handler and SDK-managed Codex/Claude worker has one task owner.
The originating turn scope coordinates termination and fixes one absolute cleanup
deadline using the existing one-second grace. Repeated controls still notify the
adapter and update the unsettled delivery outcome, but never cancel its cleanup
again or renew its deadline.

A stopped tool or auxiliary claim first revokes that scope's posting permission
and raises cancellation in its caller. Bound workers and the handler get the same
cleanup grace to unwind cooperatively; an unfinished task receives cancellation
once at expiry if none was delivered, even when nothing waits for it. Native
controls, heartbeat refusal, watchdog expiry and shutdown cancel a running turn
immediately; a worker already released for approval is left to the adapter's
room cleanup. Provider execution and recovery timeouts retain their adapter
policy and coordinate with that same owner.

Unfinished tasks remain owned after grace expires. They cannot post late replies,
change a newer turn's activity, or overlap replacement provider work. Room cleanup
reports `CleanupIncompleteError` while the worker still needs its resources;
a later existing host cleanup retry completes retirement. Worker completion alone
does not readmit the room. Successful approval release retains its original tools
and judgment until the worker actually finishes; `Turn.detach()` is still only
an outcome-ledger operation.
