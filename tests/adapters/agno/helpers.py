"""Shared helpers for the Agno adapter tests.

The adapter never calls an LLM directly: it configures the developer's Agno
agent in ``on_started`` and calls ``agent.arun(...)`` per turn. So the only thing
faked here is the Agno agent (``add_tool`` / ``arun``); everything
the adapter reads off the run is a real Agno ``RunOutput`` / ``Message`` /
``ToolExecution``. The Band side uses ``FakeAgentTools`` so calls are tracked
without a mocking framework.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agno.models.base import Model
from agno.models.message import Message
from agno.models.response import ModelResponse, ToolExecution
from agno.run.agent import RunOutput, ToolCallCompletedEvent, ToolCallStartedEvent

from band.core.types import (
    AgentInput,
    Capability,
    HistoryProvider,
    PlatformMessage,
)
from band.runtime.tools import DEFAULT_CAPABILITIES, BandTool
from band.testing import FakeAgentTools


def fake_agno_agent(
    *,
    update_memory_on_run: bool = False,
    enable_agentic_memory: bool = False,
    add_history_to_context: bool = False,
    db: object | None = None,
    response: RunOutput | None = None,
    events: list[Any] | None = None,
    model_turn: Callable[[], Awaitable[None]] | None = None,
) -> MagicMock:
    """A configured Agno agent fake whose ``arun`` yields ``response``.

    The adapter runs against this instance directly, so it carries the
    history/memory config the guards read. ``model_turn`` is the tool work the
    model does inside the run (with the room's tools bound), before ``response``.
    """
    agent = MagicMock(name="agno_agent")
    agent.update_memory_on_run = update_memory_on_run
    agent.enable_agentic_memory = enable_agentic_memory
    # Explicit falsy defaults: a bare MagicMock would expose these as truthy
    # auto-attributes and spuriously trip the history-management guard.
    agent.add_history_to_context = add_history_to_context
    agent.db = db
    agent.add_tool = MagicMock()
    # Real Agno agents default additional_context to None; mirror that.
    agent.additional_context = None
    # The adapter captures the user's tools at startup, then installs a
    # callable factory. A bare MagicMock `.tools` is itself callable and would
    # be mistaken for a user-supplied tools factory, so pin it to a list.
    agent.tools = []
    resp = response if response is not None else RunOutput()

    if events is None:
        # Non-streaming path (Emit.TOOL_CALLS off): `await agent.arun(...)`.
        async def _arun_once(*args: Any, **kwargs: Any) -> RunOutput:
            if model_turn is not None:
                await model_turn()
            return resp

        agent.arun = AsyncMock(side_effect=_arun_once)
    else:
        # Streaming path (Emit.TOOL_CALLS on): the adapter iterates
        # `agent.arun(stream=True, ...)`, which yields the run events then the
        # final RunOutput. A bare MagicMock returns an async iterator without
        # awaiting, matching how the adapter consumes the stream.
        def _arun(*args: Any, **kwargs: Any) -> Any:
            async def _stream() -> Any:
                if model_turn is not None:
                    await model_turn()
                for event in events:
                    yield event
                yield resp

            return _stream()

        agent.arun = MagicMock(side_effect=_arun)
    return agent


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


def tool_events(execution: ToolExecution) -> list[Any]:
    """The started + completed stream events Agno yields for one tool call.

    Mirrors a streamed ``arun`` (``stream_events=True``): the started event
    carries name/args/id; the completed event carries result/error. The same
    ``ToolExecution`` instance is used for both, as Agno mutates and re-emits it.
    """
    return [
        ToolCallStartedEvent(tool=execution),
        ToolCallCompletedEvent(tool=execution),
    ]


class CapturingModel(Model):
    """A real Agno model that records the messages Agno asks it to respond to.

    Lets tests assert on the actual system prompt Agno assembles (the agent's
    own instructions plus ``additional_context``), rather than the attribute the
    adapter sets. Overriding ``aresponse`` skips the provider call path, so the
    abstract invoke hooks are inert stubs.
    """

    def __init__(self, content: str = "ok", *, replies: bool = False) -> None:
        super().__init__(id="capturing", provider="fake")
        self._content = content
        # Whether the model answers through the offered band_send_message, as a
        # tool-only model does, rather than only returning its content.
        self._replies = replies
        self.captured_messages: list[Message] | None = None
        # Tool names Agno offered the model on the most recent response call,
        # so tests can assert per-run tool exposure end-to-end.
        self.captured_tool_names: list[str] | None = None

    def invoke(self, *args: Any, **kwargs: Any) -> Any: ...
    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any: ...
    def invoke_stream(self, *args: Any, **kwargs: Any) -> Any: ...
    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> Any: ...
    def _parse_provider_response(self, *args: Any, **kwargs: Any) -> Any: ...
    def _parse_provider_response_delta(self, *args: Any, **kwargs: Any) -> Any: ...

    async def aresponse(self, messages: list[Message], **kwargs: Any) -> ModelResponse:
        self.captured_messages = messages
        offered = kwargs.get("tools") or []
        self.captured_tool_names = [_tool_schema_name(t) for t in offered]
        if self._replies:
            (send,) = [
                t for t in offered if _tool_schema_name(t) == BandTool.SEND_MESSAGE
            ]
            await send.entrypoint(content=self._content, mentions=["@Alice"])
        return ModelResponse(content=self._content)

    @property
    def captured_system_prompt(self) -> str:
        """The concatenated system message(s) Agno sent to the model."""
        messages = self.captured_messages or []
        return "\n".join(
            m.content for m in messages if m.role == "system" and m.content
        )


def _tool_schema_name(tool: Any) -> str | None:
    """Best-effort tool name from an Agno Function or an OpenAI-format dict."""
    name = getattr(tool, "name", None)
    if name:
        return name
    if isinstance(tool, dict):
        return tool.get("function", {}).get("name") or tool.get("name")
    return None


class SchemaTools(FakeAgentTools):
    """FakeAgentTools that returns real OpenAI-format schemas and records the
    capability flags it was asked for (FakeAgentTools returns [] by default)."""

    def __init__(self, schemas: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._schemas = schemas
        self.schema_calls: list[dict[str, bool]] = []

    def get_openai_tool_schemas(
        self, *, capabilities: frozenset[Capability] | None = None
    ) -> list[dict[str, Any]]:
        resolved = DEFAULT_CAPABILITIES if capabilities is None else capabilities
        self.schema_calls.append(
            {
                "include_memory": Capability.MEMORY in resolved,
                "include_contacts": Capability.CONTACTS in resolved,
            }
        )
        return self._schemas


class ContactAwareTools(SchemaTools):
    """Like real AgentTools: contact tool schemas appear only when contacts are
    requested. Always exposes ``band_send_message``; adds ``band_add_contact``
    when ``Capability.CONTACTS`` is requested (or unioned in for a hub room)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__([], **kwargs)

    def get_openai_tool_schemas(
        self, *, capabilities: frozenset[Capability] | None = None
    ) -> list[dict[str, Any]]:
        resolved = DEFAULT_CAPABILITIES if capabilities is None else capabilities
        include_contacts = Capability.CONTACTS in resolved
        self.schema_calls.append(
            {
                "include_memory": Capability.MEMORY in resolved,
                "include_contacts": include_contacts,
            }
        )
        schemas = [openai_tool_schema("band_send_message")]
        if include_contacts:
            schemas.append(openai_tool_schema("band_add_contact"))
        return schemas


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
