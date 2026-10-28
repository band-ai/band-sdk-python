"""Project native OMP events onto Band messages and activity."""

from __future__ import annotations

from band.adapters.omp.contract import OmpAdapterContract
from band.adapters.omp.turnstate import OmpTurn
from band.core.content import has_visible_content
from band.core.protocols import send_event_safe
from band.core.types import Emit, MessageType
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContentKind,
    OmpEvent,
    OmpEventType,
)
from band.runtime.toolactivity import end_tool_activity, report_tool_activity
from band.runtime.tools import turn_effect


class OmpEventMixin(OmpAdapterContract):
    async def _emit_event(self, event: OmpEvent, turn: OmpTurn) -> None:
        emit_tool_calls = Emit.TOOL_CALLS in self.features.emit
        match event.type:
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
                args = event.args if isinstance(event.args, dict) else {}
                name, arguments = self._project_tool_call(
                    event.toolName, args, preserve_native_semantics=True
                )
                turn.calls[event.toolCallId] = (name, arguments)
                if emit_tool_calls:
                    await report_tool_activity(turn.tools, name)
                    await send_event_safe(
                        turn.tools,
                        self._encode_tool_call(name, arguments, event.toolCallId),
                        MessageType.TOOL_CALL,
                    )
            case OmpEventType.TOOL_END:
                call = turn.calls.pop(event.toolCallId or "", None)
                if call is None:
                    return
                name, _arguments = call
                if emit_tool_calls:
                    if turn.calls:
                        remaining_name = next(reversed(turn.calls.values()))[0]
                        await report_tool_activity(turn.tools, remaining_name)
                    else:
                        await end_tool_activity(turn.tools)
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
