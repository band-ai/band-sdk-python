"""Bridge OMP host-tool and permission callbacks into Band."""

from __future__ import annotations

import json
import logging
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from band.adapters.omp.config import OmpPermissionRequest
from band.adapters.omp.contract import OmpAdapterContract
from band.adapters.omp.toolprotocol import (
    OMP_PERMISSION_CALL_ID_PREFIX,
    OMP_PERMISSION_CANCELLED,
    OMP_PERMISSION_TOOL,
    OMP_TOOL_APPROVAL_HEADER,
    PRIVATE_PAYLOAD_UNAVAILABLE,
    _parse_approval_title,
    _text_tool_result,
    _unavailable_permission_arguments,
)
from band.adapters.omp.turnstate import OmpTurn
from band.converters.omp import OmpMetadataKey
from band.core import delivery, exceptions
from band.core.delivery import DeliveryFailedError
from band.core.exceptions import InvalidToolArgumentsError
from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import (
    PERMISSION_CANCELLED,
    Capability,
    Emit,
    MessageType,
    PermissionEventKey,
    PlatformMessage,
    ToolEventKey,
)
from band.integrations import omproomask
from band.integrations.omp import (
    OMP_DEVICE_CONTENT_KEY,
    OMP_DEVICE_KIND_EXECUTE,
    OMP_DEVICE_KIND_READ,
    OMP_DEVICE_PATH_KEYS,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
)
from band.integrations.ompprocess import normalize_native_mcp_device_call
from band.integrations.ompwire import (
    OmpContent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIMethod,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.activity import permission_wait
from band.runtime.custom_tools import execute_custom_tool, find_custom_tool
from band.runtime.tools import (
    is_image_passthrough_result,
    iter_tool_definitions,
    redact_tool_call_args,
    tool_result_text,
)

logger = logging.getLogger(__name__)


class OmpCallbackMixin(OmpAdapterContract):
    async def _execute_tool(
        self, call: OmpHostToolCall, tools: AgentToolsProtocol
    ) -> OmpToolResult:
        allowed = {item["name"] for item in self._host_tools()}
        if call.toolName not in allowed:
            return _text_tool_result(
                "Tool is not available in this session", is_error=True
            )
        try:
            custom = find_custom_tool(self._custom_tools, call.toolName)
            if custom:
                value = await execute_custom_tool(
                    custom, call.arguments, turn=tools.turn
                )
            else:
                outcome = await tools.execute_tool_call_structured(
                    call.toolName, call.arguments
                )
                if not outcome.ok:
                    return _text_tool_result(
                        outcome.error_message or str(outcome.value), is_error=True
                    )
                value = outcome.value
            if is_image_passthrough_result(call.toolName, value):
                return OmpToolResult(
                    content=[
                        OmpContent.model_validate(block) for block in value["content"]
                    ]
                )
            return _text_tool_result(tool_result_text(value))
        except DeliveryFailedError:
            raise
        except InvalidToolArgumentsError as error:
            logger.warning("OMP host tool %s rejected invalid arguments", call.toolName)
            return _text_tool_result(str(error), is_error=True)
        except Exception as error:
            logger.exception("OMP host tool %s failed", call.toolName)
            return _text_tool_result(str(error), is_error=True)

    async def _ask_room(
        self,
        request: OmpUIRequest,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> OmpUIResponse:
        """Post the questions as this turn's reply, then defer the actual answer."""
        mention = {"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}
        try:
            sent = await delivery.deliver_reply(
                tools,
                omproomask.render_room_questions(request.questions),
                mentions=[mention],
            )
        except DeliveryFailedError as error:
            if (
                isinstance(error.cause, exceptions.RoomExecutionStoppedError)
                and error.cause.room_id == room_id
            ):
                raise
            logger.warning(
                "OMP ask could not be posted (%s)", type(error.cause).__name__
            )
            return OmpUIResponse(
                answers=omproomask.undelivered_answers(request.questions, error.cause)
            )
        if sent is None:
            return OmpUIResponse(
                answers=omproomask.undelivered_answers(
                    request.questions, ValueError("the questions were empty")
                )
            )
        return OmpUIResponse(answers=omproomask.delivered_answers(request.questions))

    def _project_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        preserve_native_semantics: bool = False,
    ) -> tuple[str, dict[str, JsonValue]]:
        kind = OMP_DEVICE_KIND_EXECUTE if tool_name == "write" else OMP_DEVICE_KIND_READ
        native_name, native_arguments = tool_name, arguments
        try:
            if preserve_native_semantics:
                enabled = frozenset(
                    item.name
                    for item in iter_tool_definitions(
                        capabilities=self._host_tool_capabilities()
                    )
                )
                native_name, normalized = normalize_native_mcp_device_call(
                    tool_name, arguments, enabled, kind=kind
                )
                native_arguments = cast(dict[str, JsonValue], normalized)
            names = frozenset(
                item.name
                for item in iter_tool_definitions(capabilities=frozenset(Capability))
            )
            name, normalized = normalize_native_mcp_device_call(
                tool_name, arguments, names, kind=kind
            )
            # Bare aliases use Band's privacy policy without relabelling events.
            redaction_name = f"band_{name}" if f"band_{name}" in names else name
            privacy_arguments = cast(dict[str, JsonValue], normalized)
            redacted = redact_tool_call_args(redaction_name, privacy_arguments)
            if not preserve_native_semantics:
                return name, redacted
            if native_name == name or privacy_arguments == arguments:
                return native_name, redacted
            if redacted == privacy_arguments:
                return native_name, native_arguments
            # Keep the withheld device's path, other fields and string payload.
            return native_name, {
                **native_arguments,
                OMP_DEVICE_CONTENT_KEY: json.dumps(redacted),
            }
        except Exception:
            # Privacy failures must not discard native records.
            logger.exception("OMP could not project a native tool payload")
            if OMP_DEVICE_CONTENT_KEY in native_arguments:
                return native_name, {
                    **native_arguments,
                    OMP_DEVICE_CONTENT_KEY: PRIVATE_PAYLOAD_UNAVAILABLE,
                }
            return native_name, {"arguments": PRIVATE_PAYLOAD_UNAVAILABLE}

    @staticmethod
    def _encode_tool_call(
        name: str, arguments: dict[str, JsonValue], call_id: str
    ) -> str:
        envelope = {
            ToolEventKey.NAME: name,
            ToolEventKey.ARGS: arguments,
            ToolEventKey.TOOL_CALL_ID: call_id,
        }
        try:
            return json.dumps(envelope)
        except Exception:
            # Final encoding must not discard tool records.
            logger.exception("OMP could not encode a tool record")
            wrapper = (
                arguments.get("arguments", {})
                if name == OMP_PERMISSION_TOOL
                else arguments
            )
            safe: dict[str, JsonValue] = {"arguments": PRIVATE_PAYLOAD_UNAVAILABLE}
            if isinstance(wrapper, dict):
                for key in OMP_DEVICE_PATH_KEYS:
                    if key not in wrapper:
                        continue
                    value = wrapper[key]
                    # Large integers can fail JSON encoding too; keep only
                    # scalars whose encoding cannot hit its digit limit.
                    if (
                        value is None
                        or isinstance(value, (str, bool, float))
                        or (isinstance(value, int) and value.bit_length() <= 1024)
                    ):
                        safe[key] = value
            envelope[ToolEventKey.ARGS] = safe
            return json.dumps(envelope)

    @staticmethod
    def _encode_tool_result(
        name: str, output: str, call_id: str | None, *, is_error: bool
    ) -> str:
        return json.dumps(
            {
                ToolEventKey.NAME: name,
                ToolEventKey.OUTPUT: output,
                ToolEventKey.TOOL_CALL_ID: call_id,
                ToolEventKey.IS_ERROR: is_error,
            }
        )

    def _permission_arguments(self, title: str) -> dict[str, JsonValue]:
        """Project a UI title through the native tool normalization owner."""
        try:
            parsed = _parse_approval_title(title)
        except (ValueError, RecursionError):
            logger.exception("OMP could not parse permission arguments")
            return _unavailable_permission_arguments(title)
        if parsed is None:
            return _unavailable_permission_arguments(title)
        tool_name, arguments = parsed
        try:
            name, projected = self._project_tool_call(tool_name, arguments)
            return {
                "message": f"{OMP_TOOL_APPROVAL_HEADER}{tool_name}",
                "tool_name": name,
                "arguments": projected,
            }
        except Exception:
            logger.exception("OMP could not project permission arguments")
            return {"message": f"{OMP_TOOL_APPROVAL_HEADER}{tool_name}"}

    async def _permission(
        self,
        request: OmpUIRequest,
        room_id: str,
        session_id: str,
        turn: OmpTurn,
    ) -> OmpUIResponse:
        if request.method != OmpUIMethod.SELECT:
            logger.warning("OMP cancelled unsupported UI method %s", request.method)
            return OmpUIResponse(cancelled=True)
        tools = turn.tools
        options = tuple(request.options or ())
        choice = None
        if self._resolve_permission is not None:
            with permission_wait(tools):
                choice = await self._resolve_permission(
                    OmpPermissionRequest(
                        room_id=room_id,
                        session_id=session_id,
                        title=request.title or "",
                        options=options,
                    )
                )
        elif options == (OMP_FORM_APPROVE, OMP_FORM_DENY):
            choice = OMP_FORM_APPROVE
        response = (
            OmpUIResponse(value=choice)
            if choice is not None and choice in options
            else OmpUIResponse(cancelled=True)
        )
        # A selected UI value has no generic approval/denial meaning. Only the
        # cancelled outcome is authoritative; native tool events stay independent.
        if Emit.TOOL_CALLS in self.features.emit and response.cancelled:
            name = OMP_PERMISSION_TOOL
            call_id = f"{OMP_PERMISSION_CALL_ID_PREFIX}{uuid4().hex}"
            metadata: dict[str, object] = {
                PermissionEventKey.REQUEST: True,
                PermissionEventKey.TOOL_NAME: name,
                PermissionEventKey.TOOL_CALL_ID: call_id,
                OmpMetadataKey.SESSION_ID: session_id,
                OmpMetadataKey.REQUEST_ID: request.id,
                PermissionEventKey.AUTO_ALLOWED: False,
            }
            await send_event_safe(
                tools,
                self._encode_tool_call(
                    name, self._permission_arguments(request.title or ""), call_id
                ),
                MessageType.TOOL_CALL,
                metadata,
            )
            await send_event_safe(
                tools,
                self._encode_tool_result(
                    name, OMP_PERMISSION_CANCELLED, call_id, is_error=True
                ),
                MessageType.TOOL_RESULT,
                {**metadata, PermissionEventKey.OUTCOME: PERMISSION_CANCELLED},
            )
        return response
