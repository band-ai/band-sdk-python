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
- was settled by the adapter itself (a control reply or a busy notice)
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

## Declining with `band_no_reply`

`band_no_reply` is in every adapter's tool set, and `BASE_INSTRUCTIONS` tells
the model to call it when the latest message needs no answer: it was addressed
to someone else, it is an FYI or an acknowledgement, or someone already answered
it. The call is local only; its `reason` goes to the agent log.

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
leaves the verdict to the adapter, which reports at the turn's normal end. A
cancelled turn reports nothing:

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
