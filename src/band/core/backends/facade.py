"""Helpers for provider-adapter façades over ``NativeToolLoopBackend``."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar

from band.core.contracts import (
    EnvelopedTurnEvent,
    ModelMessage,
    ModelMessageRole,
    ModelToolCall,
    ToolCallEvent,
    ToolResultEvent,
    TurnEvent,
)
from band.core.backends.native import NativeToolLoopBackend
from band.core.backends.observing import turn_context
from band.core.protocols import AgentToolsProtocol, EventSink, RunContext
from band.core.simple_adapter import SimpleAdapter
from band.core.run.cancellation import NeverCancelled
from band.core.run.context import SimpleRunContext
from band.core.types import AdapterFeatures, Emit, PlatformMessage

if TYPE_CHECKING:
    from band.core.backends.native import ExecuteFn
from band.core.run.sink import RecordingEventSink
from band.runtime.narration import tool_call_content, tool_result_content
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    find_custom_tool,
)

logger = logging.getLogger(__name__)


def model_messages_from_anthropic(
    history: Sequence[dict[str, Any]],
) -> list[ModelMessage]:
    """Project Anthropic dict history into ``ModelMessage`` rows."""
    out: list[ModelMessage] = []
    for entry in history:
        role = (
            ModelMessageRole.ASSISTANT
            if entry.get("role") == "assistant"
            else ModelMessageRole.USER
        )
        out.append(ModelMessage(role=role, content=entry.get("content", "")))
    return out


def anthropic_dicts_from_model_messages(
    messages: Sequence[ModelMessage],
) -> list[dict[str, Any]]:
    """Project ``ModelMessage`` session back to Anthropic dict history."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role is ModelMessageRole.SYSTEM:
            continue
        role = "assistant" if message.role is ModelMessageRole.ASSISTANT else "user"
        out.append({"role": role, "content": message.content})
    return out


def model_messages_from_gemini(history: Sequence[Any]) -> list[ModelMessage]:
    """Project Gemini ``Content`` history into ``ModelMessage`` rows."""
    from google.genai import types

    out: list[ModelMessage] = []
    for entry in history:
        if isinstance(entry, types.Content):
            role = (
                ModelMessageRole.ASSISTANT
                if entry.role == "model"
                else ModelMessageRole.USER
            )
            out.append(ModelMessage(role=role, content=entry))
            continue
        if isinstance(entry, dict):
            role = (
                ModelMessageRole.ASSISTANT
                if entry.get("role") in {"assistant", "model"}
                else ModelMessageRole.USER
            )
            out.append(ModelMessage(role=role, content=entry.get("content", "")))
    return out


def gemini_contents_from_model_messages(messages: Sequence[ModelMessage]) -> list[Any]:
    """Project ``ModelMessage`` session back to Gemini ``Content`` history."""
    from google.genai import types

    out: list[Any] = []
    for message in messages:
        if message.role is ModelMessageRole.SYSTEM:
            continue
        if isinstance(message.content, types.Content):
            out.append(message.content)
            continue
        role = "model" if message.role is ModelMessageRole.ASSISTANT else "user"
        out.append(
            types.Content(
                role=role, parts=[types.Part.from_text(text=str(message.content))]
            )
        )
    return out


def facade_run_context(
    tools: AgentToolsProtocol, features: AdapterFeatures
) -> SimpleRunContext:
    """The turn context both provider façades hand to their tool loop.

    Identical for every provider: the turn's tools, a sink that mirrors tool
    events into the room when the adapter emits execution, and the outer turn's
    cancellation token so cancelling the turn stops the inner loop. The inner
    sink is the outer turn's own, so a provider turn is observable on the
    published stream instead of emitting into a sink nothing reads.
    """
    turn = turn_context(tools)
    return SimpleRunContext(
        tools=tools,
        events=ExecutionBridgingSink(
            tools=tools,
            enabled=Emit.EXECUTION in features.emit,
            inner=turn.events if turn is not None else RecordingEventSink(),
        ),
        cancellation=turn.cancellation if turn is not None else NeverCancelled(),
    )


@dataclass
class ExecutionBridgingSink:
    """Forward tool call/result events to ``tools.send_event`` when enabled."""

    tools: AgentToolsProtocol
    enabled: bool
    inner: EventSink = field(default_factory=RecordingEventSink)

    @property
    def events(self) -> Sequence[EnvelopedTurnEvent]:
        return getattr(self.inner, "events", ())

    async def emit(self, event: TurnEvent) -> None:
        await self.inner.emit(event)
        if not self.enabled:
            return
        match event:
            case ToolCallEvent(
                tool_name=name, tool_call_id=call_id, arguments=arguments
            ):
                try:
                    await self.tools.send_event(
                        content=tool_call_content(
                            name, args=arguments, tool_call_id=call_id
                        ),
                        message_type="tool_call",
                    )
                except Exception as exc:
                    logger.warning("Failed to send tool_call event: %s", exc)
            case ToolResultEvent(
                tool_name=name,
                tool_call_id=call_id,
                content=content,
                status=status,
            ):
                try:
                    await self.tools.send_event(
                        content=tool_result_content(
                            name,
                            output=content,
                            tool_call_id=call_id,
                            is_error=None
                            if status is None
                            else status.value == "failed",
                        ),
                        message_type="tool_result",
                    )
                except Exception as exc:
                    logger.warning("Failed to send tool_result event: %s", exc)
            case _:
                return


def make_custom_tool_executor(
    custom_tools: Sequence[CustomToolDef],
) -> ExecuteFn:
    """Build an ``execute_override`` that prefers custom tools, then platform tools.

    Custom tools become string outcomes; platform tools retain their structured
    outcome so delivery evidence never treats a failed post as successful.

    Bad arguments reach the model as a failed outcome rather than ending the
    turn, so it can correct itself. ``execute_custom_tool`` already renders a
    validation failure as ``Invalid arguments for <tool>: ...``; a raw
    ``ValidationError`` from a handler that builds its own models is caught
    here and rendered the same way.
    """
    from pydantic import ValidationError

    from band.runtime.custom_tools import format_validation_error
    from band.runtime.tools import ToolCallOutcome

    def failed(message: str) -> ToolCallOutcome:
        return ToolCallOutcome(value=message, ok=False, error_message=message)

    async def execute(context: RunContext, call: ModelToolCall) -> ToolCallOutcome:
        tool_name = call.name
        tool_input = dict(call.arguments)
        try:
            custom = find_custom_tool(list(custom_tools), tool_name)
            if custom:
                result = await execute_custom_tool(custom, tool_input)
                result_str = (
                    json.dumps(result, default=str)
                    if not isinstance(result, str)
                    else result
                )
                return ToolCallOutcome(value=result_str, ok=True)
            return await context.tools.execute_tool_call_structured(
                tool_name, tool_input
            )
        except ValidationError as exc:
            return failed(
                f"Invalid arguments for {tool_name}: {format_validation_error(exc)}"
            )
        except Exception as exc:
            return failed(f"Error: {exc}")

    return execute


H = TypeVar("H")
# Provider-native tool schemas; always a sequence the provider forwards.
TSchemas = TypeVar("TSchemas", bound=Sequence[Any])


class NativeProviderAdapter(SimpleAdapter[H], Generic[H, TSchemas], ABC):
    """Shared turn body for adapters that drive a ``NativeToolLoopBackend``.

    Anthropic and Gemini differ in three places — the framework history type,
    the provider-native tool schemas, and how a bootstrap transcript seeds a
    session. Everything else about a turn is the same, so it lives here once.

    ``TSchemas`` is what keeps the two apart at the type level: the schemas a
    subclass builds are the schemas its own provider is handed, so an
    Anthropic ``ToolParam`` list cannot reach the Gemini provider.
    """

    _backend: NativeToolLoopBackend
    provider_label: ClassVar[str] = "the model"

    @abstractmethod
    def _build_tools(self, tools: AgentToolsProtocol) -> TSchemas:
        """The provider-native tool schemas for this turn."""

    @abstractmethod
    def _seed_session(self, history: H) -> list[ModelMessage]:
        """Project a bootstrap transcript into the backend's session shape."""

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: H,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run one turn through the tool loop."""
        if is_session_bootstrap:
            seed = self._seed_session(history)
            self._backend.bind_session(room_id, seed)
            logger.info("Room %s: loaded %s historical messages", room_id, len(seed))
        elif not self._backend.has_session(room_id):
            self._backend.bind_session(room_id, [])

        try:
            await self._backend.run(
                session_id=room_id,
                message=msg,
                context=facade_run_context(tools, self.features),
                participants_context=participants_msg,
                contacts_context=contacts_msg,
                tools=self._build_tools(tools),
            )
        except Exception as exc:
            logger.exception("Error calling %s: %s", self.provider_label, exc)
            await self._report_error(tools, str(exc))
            raise
        finally:
            # The backend's per-room tally covers finished, failed and
            # interrupted turns alike, so teardown never has to reconstruct
            # it — and reading it by room keeps a concurrent room's turn,
            # which may start during the error report above, out of it.
            await self.emit_usage(tools, self._backend.last_turn_usage(room_id))
            dropped = self._backend.trim_session(room_id)
            if dropped:
                logger.debug("Room %s: trimmed %s oldest messages", room_id, dropped)

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send an error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception as exc:
            logger.warning("Failed to send error event: %s", exc)

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's session when the agent leaves it."""
        await self._backend.close_session(room_id)

    async def cleanup_all(self) -> None:
        """Release the provider's client when the agent stops.

        Nothing else reaches it: the agent closes its backend, which is the
        adapter shim, which calls this. Without it the provider's connection
        pool outlives every agent in the process.
        """
        await self._backend.aclose()
