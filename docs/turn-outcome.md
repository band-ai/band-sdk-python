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
- was settled by the adapter itself (a control reply, a busy notice)
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

`tests/framework_conformance/test_reply_boundary.py` pins every `send_message`,
`deliver_reply` and `relay_reply` call outside the tool implementations, per
file with the reason it carries the model's words, so a new one fails until it
is justified.

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
