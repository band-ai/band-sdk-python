"""Shared helpers for the NOOA adapter tests.

The adapter runs a real NOOA agent loop; only the model is scripted, through
NOOA's own ``FakeLLMClient``. The Band side uses ``FakeAgentTools``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from nooa.config.execution_config import ExecutionConfig
from nooa.unifiedllm import FakeLLMClient
from pydantic import BaseModel, Field
from typing_extensions import Unpack

from band.adapters.nooa import (
    TOOLS_BLOCK,
    NooaAdapter,
    NooaAdapterConfig,
    NooaRoomAgent,
)
from band.core.types import FeatureKwargs, PlatformMessage, ToolEventKey
from band.runtime.custom_tools import CustomToolDef, declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import FakeAgentTools, events_of_type
from band.testing.nooa import code_turn, result_turn, room_call, scripted_room_agent

ROOM_ID = "room-nooa"

_TOOL_METHOD = re.compile(r"async def (\w+)\(")


class OrderInput(BaseModel):
    """Look up an order by id."""

    order_id: str = Field(description="The order to look up")


@declares_turn_effect(TurnEffect.ACT)
def find_order(args: OrderInput) -> dict[str, str]:
    """A custom tool whose successful call is real work."""
    return {"order_id": args.order_id, "status": "shipped"}


class ImageFileTools(FakeAgentTools):
    """Fake tools whose room file read returns one PNG as MCP image content."""

    PNG_BASE64 = "iVBORw0KGgo="

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        return {
            "content": [
                {"type": "image", "data": self.PNG_BASE64, "mimeType": "image/png"}
            ]
        }


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
    return type(NooaRoomAgent)(
        "ConfiguredRoomAgent",
        (NooaRoomAgent,),
        {"__doc__": "You are a configured test agent.", "__module__": __name__},
        llm=FakeLLMClient(),
        execution=execution,
    )


def declining_turns(count: int) -> FakeLLMClient:
    """A model that declines *count* messages in a row."""
    return FakeLLMClient(
        [
            turn
            for _ in range(count)
            for turn in (code_turn(room_call("band_no_reply")), result_turn())
        ]
    )


def failing_llm(reason: str) -> FakeLLMClient:
    """A model whose every call fails with ``RuntimeError(reason)``."""

    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(reason)

    llm = FakeLLMClient()
    llm.acall = fail  # type: ignore[method-assign]
    return llm


async def failing_report(*args: object, **kwargs: object) -> None:
    """A ``send_failure`` whose platform call itself fails."""
    raise ConnectionError("platform unreachable")


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
        agent=scripted_room_agent(llm),
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
) -> None:
    """Run one judged turn, as the runtime does for a delivered message."""
    message = message or room_message()
    await adapter.run_judged_turn(
        msg=message,
        tools=tools,
        history=list(history),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=message.room_id,
    )


def prompt_text(llm: FakeLLMClient) -> str:
    """Everything the model was sent on its latest call, as one string."""
    return "\n".join(str(message.get("content")) for message in llm.last_messages)


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
        if event["message_type"] in {"tool_call", "tool_result"}
    ]


def tool_results(tools: FakeAgentTools) -> list[dict[str, Any]]:
    """The decoded payloads of every ``tool_result`` event."""
    return [
        json.loads(event["content"]) for event in events_of_type(tools, "tool_result")
    ]
