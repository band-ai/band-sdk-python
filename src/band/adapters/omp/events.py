"""Project native OMP events onto Band messages and activity."""

from __future__ import annotations

from band.adapters.omp.contract import OmpAdapterContract
from band.adapters.omp.turnstate import OmpCall, OmpTurn
from band.core.content import has_visible_content
from band.core.protocols import send_event_safe
from band.core.types import Emit, MessageType
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContentKind,
    OmpEvent,
    OmpEventType,
)
from band.runtime.toolactivity import (
    end_activity,
    start_memory_activity,
    start_native_tool_activity,
    start_tool_activity,
)
from band.runtime.tools import turn_effect


class OmpEventMixin(OmpAdapterContract):
    async def _emit_event(self, event: OmpEvent, turn: OmpTurn) -> None:
        emit_tool_calls = Emit.TOOL_CALLS in self.features.emit
        match event.type:
            case OmpEventType.AUTO_COMPACTION_START:
                if turn.compaction is None:
                    turn.compaction = object()
                    await start_memory_activity(turn.tools, turn.compaction)
            case OmpEventType.AUTO_COMPACTION_END:
                if turn.compaction is not None:
                    token, turn.compaction = turn.compaction, None
                    await end_activity(turn.tools, token)
            case OmpEventType.MESSAGE_END:
                message = event.message
                if message is None or message.role != OMP_ASSISTANT_ROLE:
                    return
                if isinstance(message.content, str):
                    if visible := _visible_text(message.content):
                        turn.closing_text.append(visible)
                    return
                for block in message.content:
                    if block.type == OmpContentKind.THINKING:
                        thinking = _visible_text(block.thinking)
                        if thinking and Emit.THOUGHTS in self.features.emit:
                            await send_event_safe(
                                turn.tools, thinking, MessageType.THOUGHT
                            )
                    elif block.type == OmpContentKind.TEXT:
                        text = _visible_text(block.text)
                        if text:
                            turn.closing_text.append(text)
            case OmpEventType.TOOL_START:
                if not event.toolCallId or not event.toolName:
                    return
                if event.toolCallId in turn.calls:
                    return
                args = event.args if isinstance(event.args, dict) else {}
                name, arguments = self._project_tool_call(
                    event.toolName, args, preserve_native_semantics=True
                )
                turn.calls[event.toolCallId] = call = OmpCall(name)
                if event.toolName in self._band_host_names:
                    await start_tool_activity(turn.tools, call.token, name)
                else:
                    await start_native_tool_activity(turn.tools, call.token, name)
                if emit_tool_calls:
                    await send_event_safe(
                        turn.tools,
                        self._encode_tool_call(name, arguments, event.toolCallId),
                        MessageType.TOOL_CALL,
                    )
            case OmpEventType.TOOL_END:
                call = turn.calls.pop(event.toolCallId or "", None)
                if call is None:
                    return
                name = call.name
                await end_activity(turn.tools, call.token)
                if not self.config.inject_band_tools and not event.isError:
                    turn.tools.turn.record(turn_effect(name))
                if emit_tool_calls:
                    output = (
                        "\n".join(
                            block.text
                            or ("[image]" if block.type == OmpContentKind.IMAGE else "")
                            for block in event.result.content
                        )
                        if event.result
                        else ""
                    )
                    await send_event_safe(
                        turn.tools,
                        self._encode_tool_result(
                            name,
                            output,
                            event.toolCallId,
                            is_error=bool(event.isError),
                        ),
                        MessageType.TOOL_RESULT,
                    )


def _visible_text(value: str | None) -> str | None:
    if value is not None and has_visible_content(value):
        return value
    return None
