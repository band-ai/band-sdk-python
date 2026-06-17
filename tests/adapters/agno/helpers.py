"""Shared helpers for the Agno adapter tests.

The adapter never calls an LLM directly: it deep-copies the developer's Agno
agent in ``on_started`` and calls ``agent.arun(...)`` per turn. So the only thing
faked here is the Agno agent (``deep_copy`` / ``add_tool`` / ``arun``); everything
the adapter reads off the run is a real Agno ``RunOutput`` / ``Message`` /
``ToolExecution``. The Band side uses ``FakeAgentTools`` so calls are tracked
without a mocking framework.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agno.models.message import Message
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput

from band.adapters.agno import AgnoAdapter
from band.core.types import (
    AdapterFeatures,
    AgentInput,
    HistoryProvider,
    PlatformMessage,
)
from band.testing import FakeAgentTools


def make_agno_agent(
    *,
    update_memory_on_run: bool = False,
    enable_agentic_memory: bool = False,
    response: RunOutput | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (source_agent, copied_agent) fakes.

    ``deep_copy()`` returns the copy, mirroring how the adapter runs against a
    copy of the developer's agent. The copy's ``arun`` yields ``response``.
    """
    source = MagicMock(name="source_agent")
    source.update_memory_on_run = update_memory_on_run
    source.enable_agentic_memory = enable_agentic_memory

    copy = MagicMock(name="copied_agent")
    copy.add_tool = MagicMock()
    copy.arun = AsyncMock(
        return_value=response if response is not None else RunOutput()
    )
    source.deep_copy = MagicMock(return_value=copy)
    return source, copy


def tool_execution(
    name: str,
    *,
    call_id: str = "tc_1",
    args: dict[str, Any] | None = None,
    result: str = "",
    error: bool = False,
) -> ToolExecution:
    return ToolExecution(
        tool_name=name,
        tool_call_id=call_id,
        tool_args=args or {},
        result=result,
        tool_call_error=error,
    )


async def started(
    response: RunOutput | None = None,
    *,
    features: AdapterFeatures | None = None,
) -> tuple[AgnoAdapter, MagicMock]:
    """Build an adapter past ``on_started`` and return (adapter, copied_agent)."""
    source, copy = make_agno_agent(response=response)
    adapter = AgnoAdapter(source, features=features)
    await adapter.on_started("TestBot", "desc")
    return adapter, copy


class SchemaTools(FakeAgentTools):
    """FakeAgentTools that returns real OpenAI-format schemas and records the
    capability flags it was asked for (FakeAgentTools returns [] by default)."""

    def __init__(self, schemas: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._schemas = schemas
        self.schema_calls: list[dict[str, bool]] = []

    def get_openai_tool_schemas(
        self, *, include_memory: bool = False, include_contacts: bool = True
    ) -> list[dict[str, Any]]:
        self.schema_calls.append(
            {"include_memory": include_memory, "include_contacts": include_contacts}
        )
        return self._schemas


def openai_tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def make_agent_input(
    msg: PlatformMessage,
    raw: list[dict[str, Any]],
    *,
    is_session_bootstrap: bool,
    participants_msg: str | None = None,
    contacts_msg: str | None = None,
    tools: FakeAgentTools | None = None,
) -> AgentInput:
    """Build an AgentInput so tests drive the real on_event -> converter path."""
    return AgentInput(
        msg=msg,
        tools=tools or FakeAgentTools(),
        history=HistoryProvider(raw=raw),
        participants_msg=participants_msg,
        contacts_msg=contacts_msg,
        is_session_bootstrap=is_session_bootstrap,
        room_id=msg.room_id,
    )


def run_input(copy: MagicMock) -> list[Message]:
    """The exact list[Message] the faked Agno agent received via arun(input=...)."""
    return copy.arun.await_args.kwargs["input"]


def platform_msg(
    msg_id: str,
    content: str,
    *,
    sender_type: str = "User",
    sender_name: str = "Alice",
    message_type: str = "text",
) -> dict[str, Any]:
    """A platform-shaped history dict, as the REST context API would return it."""
    return {
        "id": msg_id,
        "content": content,
        "sender_id": f"id-{msg_id}",
        "sender_type": sender_type,
        "sender_name": sender_name,
        "message_type": message_type,
        "metadata": {},
    }
