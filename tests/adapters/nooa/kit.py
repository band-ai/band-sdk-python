"""Shared helpers for the NOOA adapter tests.

The adapter runs a real NOOA agent loop; only the model is scripted, through
NOOA's own ``FakeLLMClient``. The Band side uses ``FakeAgentTools``.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from nooa import Agent
from nooa.config.execution_config import ExecutionConfig
from nooa.unifiedllm import FakeLLMClient, LLMResponse
from pydantic import BaseModel, Field
from typing_extensions import Unpack

from band.adapters.nooa import NooaAdapter, NooaAdapterConfig, NooaRoomAgent
from band.adapters.nooa.adapter import TOOLS_BLOCK
from band.core.types import FeatureKwargs, MessageType, PlatformMessage, ToolEventKey
from band.runtime.custom_tools import CustomToolDef, declares_turn_effect
from band.runtime.tools import BandTool, ToolCallOutcome, TurnEffect
from band.testing import FakeAgentTools, events_of_type
from band.testing.nooa import (
    code_turn,
    result_turn,
    room_agent_class,
    room_call,
    scripted_llm,
)

ROOM_ID = "room-nooa"

_TOOL_METHOD = re.compile(r"async def (\w+)\(")


class OrderInput(BaseModel):
    """Look up an order by id."""

    order_id: str = Field(description="The order to look up")


@declares_turn_effect(TurnEffect.ACT)
def find_order(args: OrderInput) -> dict[str, str]:
    """A custom tool whose successful call is real work."""
    return {"order_id": args.order_id, "status": "shipped"}


class OutcomeTools(FakeAgentTools):
    """Fake tools whose dispatch of one tool returns a set outcome.

    ``FakeAgentTools`` dispatches every tool as a plain success, and the adapter
    dispatches through ``execute_tool_call_structured``, so that is the seam a
    test scripts.
    """

    def __init__(self, tool_name: str, outcome: ToolCallOutcome) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._outcome = outcome

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        if tool_name != self._tool_name:
            return await super().execute_tool_call_structured(tool_name, arguments)
        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        return self._outcome


PNG_RESULT = {
    "content": [{"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"}]
}


def image_file_tools() -> OutcomeTools:
    """Fake tools whose room file read returns one PNG as MCP image content."""
    return OutcomeTools(
        BandTool.READ_ROOM_FILE, ToolCallOutcome(value=PNG_RESULT, ok=True)
    )


def rejecting_send_tools(reason: str) -> OutcomeTools:
    """Fake tools on which the platform rejects every message the agent sends."""
    return OutcomeTools(
        BandTool.SEND_MESSAGE,
        ToolCallOutcome(value=reason, ok=False, error_message=reason),
    )


def dispatched(tools: FakeAgentTools) -> list[str]:
    """The tools the agent dispatched, in order."""
    return [call["tool_name"] for call in tools.tool_calls]


class ClassInput(BaseModel):
    """A tool whose derived name, ``class``, is a Python keyword."""


class LookupOrderInput(BaseModel):
    """Look up an order whose id field is read under an alias."""

    order_id: str = Field(alias="orderId", description="The order to look up")


def lookup_order(args: LookupOrderInput) -> str:
    """A custom tool whose input model validates by alias."""
    return f"{args.order_id} is shipped"


class TaggedInput(BaseModel):
    """Tag an order."""

    order_id: str = Field(description="The order to tag")
    # A factory that needs the validated data cannot be called just to document it.
    tags: list[str] = Field(
        default_factory=lambda data: [data["order_id"]], description="Tags to add"
    )


def tag_order(args: TaggedInput) -> list[str]:
    """A custom tool whose argument has a factory default."""
    return args.tags


def room_agent_with_execution(execution: ExecutionConfig) -> type[NooaRoomAgent]:
    """A room agent class declared with NOOA's class-level ``execution`` setting."""
    return room_agent_class(FakeLLMClient(), execution=execution)


def carrying(name: str, value: object) -> dict[str, Any]:
    """Class namespace that gives every agent instance *value* as ``self.<name>``.

    Scripted cells reach test coordination objects through it.
    """

    def __init__(agent: NooaRoomAgent) -> None:
        NooaRoomAgent.__init__(agent)
        setattr(agent, name, value)

    return {"__init__": __init__, "__annotations__": {name: type(value)}}


def background_room_call(tool_name: str) -> str:
    """Python that starts a ``self.room`` call without waiting for it."""
    return f"asyncio.create_task(self.room.{tool_name}())"


class SlowInput(BaseModel):
    """A custom tool that finishes only once its gate opens."""


class Gate:
    """Holds a custom tool open, and records how it ended."""

    def __init__(self) -> None:
        self.opened = asyncio.Event()
        self.entered = asyncio.Event()
        self.committed = False
        self.cancelled = False

    async def handler(self) -> str:
        self.entered.set()
        try:
            await self.opened.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.committed = True
        return "done"

    def room_agent(self) -> type[NooaRoomAgent]:
        """A room agent that starts the gated tool, waits until it runs, then declines."""
        llm = scripted_llm(
            f"pending = {background_room_call('slow')}\n"
            "await self.gate.entered.wait()\n"
            f"{room_call(BandTool.NO_REPLY)}"
        )
        return room_agent_class(llm, namespace=carrying("gate", self))


class LeftoverReply:
    """A reply the model leaves running in one turn and releases in the next.

    Turn one starts a task that waits for ``release`` and then replies; turn
    two opens the gate, awaits that task, and records how it ended. The model's
    code reaches this object as ``self.leftover``.
    """

    FIRST_TURN = (
        "async def reply_later():\n"
        "    await self.leftover.release.wait()\n"
        f"    {room_call(BandTool.SEND_MESSAGE, {'content': 'late', 'mentions': ['Alice']})}\n"
        "self.leftover.task = asyncio.create_task(reply_later())\n"
        f"{room_call(BandTool.NO_REPLY)}"
    )
    SECOND_TURN = (
        "self.leftover.release.set()\n"
        "try:\n"
        "    await self.leftover.task\n"
        "    self.leftover.outcome = 'replied'\n"
        "except Exception as error:\n"
        "    self.leftover.outcome = f'{type(error).__name__}: {error}'\n"
        f"{room_call(BandTool.NO_REPLY)}"
    )

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.task: asyncio.Task[Any] | None = None
        self.outcome: str | None = None

    def room_agent(self) -> type[NooaRoomAgent]:
        """A room agent class whose instances carry this scenario."""
        llm = FakeLLMClient(
            [
                code_turn(self.FIRST_TURN),
                result_turn(),
                code_turn(self.SECOND_TURN),
                result_turn(),
            ]
        )
        return room_agent_class(llm, namespace=carrying("leftover", self))


CHILD_USAGE = {"prompt_tokens": 100, "completion_tokens": 15}


def delegating_room_agent() -> type[NooaRoomAgent]:
    """A room agent whose ``respond`` delegates to a scripted child agent.

    The parent makes no model call of its own, so every token the turn spends
    is the child's.
    """
    child_class = room_agent_class(
        FakeLLMClient(
            [code_turn("print('working')", usage=CHILD_USAGE), result_turn()]
        ),
        base=Agent,
        name="Child",
        doc="You are a helper agent.",
        namespace={"work": child_work},
    )

    async def respond(self: NooaRoomAgent, message: str) -> None:
        await child_class().work()
        await self.room.band_no_reply()

    return room_agent_class(
        FakeLLMClient(), name="Parent", namespace={"respond": respond}
    )


async def child_work(self: Agent) -> None:
    """Do the delegated work."""
    ...  # noqa: PIE790 -- NOOA hands an ellipsis-bodied method to the model


def declining_turns(count: int) -> FakeLLMClient:
    """A model that declines *count* messages in a row."""
    return FakeLLMClient(
        [
            turn
            for _ in range(count)
            for turn in (code_turn(room_call("band_no_reply")), result_turn())
        ]
    )


def failing_llm(reason: str, *before: LLMResponse) -> FakeLLMClient:
    """A model that answers with *before*, then fails every call with *reason*."""
    llm = FakeLLMClient(list(before))
    answer = llm.acall

    async def acall(*args: Any, **kwargs: Any) -> LLMResponse:
        if llm._response_queue:
            return await answer(*args, **kwargs)
        raise RuntimeError(reason)

    llm.acall = acall  # type: ignore[method-assign]
    return llm


def room_message(
    content: str = "@Agent hello", *, room_id: str = ROOM_ID
) -> PlatformMessage:
    """A user message addressed to the agent in *room_id*."""
    return PlatformMessage(
        id=f"msg-{room_id}-{content}",
        room_id=room_id,
        content=content,
        sender_id="user-alice",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


async def started_adapter(
    llm: FakeLLMClient,
    *,
    config: NooaAdapterConfig | None = None,
    additional_tools: list[CustomToolDef] | None = None,
    **features: Unpack[FeatureKwargs],
) -> NooaAdapter:
    """An adapter around a scripted agent, past ``on_started``."""
    adapter = NooaAdapter(
        config,
        agent=room_agent_class(llm),
        additional_tools=additional_tools,
        **features,
    )
    await adapter.on_started("Agent", "A test agent")
    return adapter


async def run_turn(
    adapter: NooaAdapter,
    tools: FakeAgentTools,
    *,
    message: PlatformMessage | None = None,
    history: Sequence[str] = (),
    is_session_bootstrap: bool = True,
    participants_msg: str | None = None,
) -> None:
    """Run one judged turn, as the runtime does for a delivered message."""
    message = message or room_message()
    await adapter.run_judged_turn(
        msg=message,
        tools=tools,
        history=list(history),
        participants_msg=participants_msg,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=message.room_id,
    )


def prompt_text(llm: FakeLLMClient) -> str:
    """Everything the model was sent on its latest call, as one string."""
    return "\n".join(str(message.get("content")) for message in llm.last_messages)


def execution_context(llm: FakeLLMClient) -> str:
    """The names NOOA told the model are already in scope for its code."""
    text = prompt_text(llm)
    return text[text.index("<execution_context>") : text.index("</execution_context>")]


def advertised_tools(llm: FakeLLMClient) -> list[str]:
    """The ``self.room`` methods listed in the tools block of the latest prompt."""
    text = prompt_text(llm)
    block = text[text.index(f"<{TOOLS_BLOCK}>") : text.index(f"</{TOOLS_BLOCK}>")]
    return _TOOL_METHOD.findall(block)


def tool_events(tools: FakeAgentTools) -> list[str]:
    """``tool_call``/``tool_result`` events as ``"<type> <tool>"``, in order."""
    return [
        f"{event['message_type']} {json.loads(event['content'])[ToolEventKey.NAME]}"
        for event in tools.events_sent
        if event["message_type"] in {MessageType.TOOL_CALL, MessageType.TOOL_RESULT}
    ]


def tool_results(tools: FakeAgentTools) -> list[dict[str, Any]]:
    """The decoded payloads of every ``tool_result`` event."""
    return [
        json.loads(event["content"])
        for event in events_of_type(tools, MessageType.TOOL_RESULT)
    ]
