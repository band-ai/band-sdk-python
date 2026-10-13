"""The ``self.room`` tool surface a NOOA model calls from its generated code.

Each Band tool, and each portable custom tool, becomes an async method on a
``RoomTools`` subclass whose signature and docstring are rendered from the
tool's input model. A call works only while the turn that made it is running.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import keyword
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast
from uuid import uuid4

from nooa import Image, hidden
from nooa.agentdoc import doc
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import MessageType, ToolEventKey
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
    reject_conflicting_tool_names,
)
from band.runtime.tools import (
    ImageContentResult,
    ToolDefinition,
    decode_image_blocks,
    docstring_with_args,
    image_block_placeholder,
    image_passthrough_result,
    redact_tool_call_args,
    serialize_tool_result,
)

logger = logging.getLogger(__name__)

ToolRunner = Callable[[AgentToolsProtocol, dict[str, Any]], Awaitable[object]]


@dataclass(frozen=True)
class RoomToolSpec:
    """One tool the model can call as ``await self.room.<name>(...)``."""

    name: str
    input_model: type[BaseModel]
    run: ToolRunner


@dataclass
class TurnBinding:
    """One turn's tools for one room, and the tool calls that turn started.

    The turn owns its calls: when it ends, calls still running are cancelled
    and awaited, so none can commit an effect after the turn is judged.
    """

    room: RoomTools
    tools: AgentToolsProtocol
    open: bool = True
    in_flight: set[asyncio.Task[Any]] = field(default_factory=set)

    def start(self, call: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(call)
        self.in_flight.add(task)
        task.add_done_callback(self.in_flight.discard)
        return task

    async def close(self) -> None:
        self.open = False
        for task in self.in_flight:
            task.cancel()
        await asyncio.gather(*self.in_flight, return_exceptions=True)


# Context-local, not an attribute of the room's RoomTools: a task the model's code
# starts copies the context of the turn that started it, so after that turn ends
# it still sees that turn's (closed) binding rather than whichever turn is current.
_turn_binding: ContextVar[TurnBinding | None] = ContextVar(
    "nooa_room_turn_binding", default=None
)


def caller_is_cancelled() -> bool:
    """Whether the task awaiting a tool call is itself being cancelled."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


class RoomTools:
    """Band tools for the current room. Every method is async: `await self.room.<method>(...)`."""

    _specs: ClassVar[Mapping[str, RoomToolSpec]] = {}

    def __init__(self, *, emit_tool_calls: bool) -> None:
        self._emit_tool_calls = emit_tool_calls

    def __repr__(self) -> str:
        return f"RoomTools({', '.join(self._specs)})"

    @hidden
    @asynccontextmanager
    async def bound_to(self, tools: AgentToolsProtocol) -> AsyncIterator[None]:
        """Route this turn's calls to its tools; refuse or cancel them afterwards."""
        binding = TurnBinding(self, tools)
        token = _turn_binding.set(binding)
        try:
            yield
        finally:
            await binding.close()
            _turn_binding.reset(token)

    def _binding(self, name: str) -> TurnBinding:
        """The binding of the turn this call belongs to, if it is still running."""
        binding = _turn_binding.get()
        if binding is None or not binding.open or binding.room is not self:
            raise BandToolError(
                f"{name} is only available while a Band room message is being handled"
            )
        return binding

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        binding = self._binding(name)
        call = binding.start(self._call(binding.tools, name, arguments))
        try:
            return await call
        except asyncio.CancelledError:
            if caller_is_cancelled():
                raise
            raise BandToolError(
                f"{name} was cancelled because its turn ended first"
            ) from None

    async def _call(
        self, tools: AgentToolsProtocol, name: str, arguments: dict[str, Any]
    ) -> Any:
        """Run one tool call and report it, inside the turn that owns it."""
        call_id = uuid4().hex
        await self._report(
            tools,
            MessageType.TOOL_CALL,
            {
                ToolEventKey.NAME: name,
                ToolEventKey.ARGS: redact_tool_call_args(name, arguments),
                ToolEventKey.TOOL_CALL_ID: call_id,
            },
        )
        try:
            value = serialize_tool_result(await self._specs[name].run(tools, arguments))
        except asyncio.CancelledError:
            message = f"{name} was cancelled because its turn ended first"
            await self._report_result(tools, name, call_id, message, is_error=True)
            raise
        except Exception as error:
            message = f"{name} failed: {error}"
            logger.warning("Band tool %s failed: %s", name, error)
            await self._report_result(tools, name, call_id, message, is_error=True)
            raise BandToolError(message) from error
        if (image_result := image_passthrough_result(name, value)) is not None:
            images = images_from_result(image_result)
            output = image_block_placeholder(len(images))
            await self._report_result(tools, name, call_id, output, is_error=False)
            return images
        await self._report_result(tools, name, call_id, value, is_error=False)
        return value

    async def _report_result(
        self,
        tools: AgentToolsProtocol,
        name: str,
        call_id: str,
        output: object,
        *,
        is_error: bool,
    ) -> None:
        await self._report(
            tools,
            MessageType.TOOL_RESULT,
            {
                ToolEventKey.NAME: name,
                ToolEventKey.OUTPUT: output,
                ToolEventKey.TOOL_CALL_ID: call_id,
                ToolEventKey.IS_ERROR: is_error,
            },
        )

    async def _report(
        self,
        tools: AgentToolsProtocol,
        message_type: MessageType,
        payload: Mapping[ToolEventKey, object],
    ) -> None:
        if not self._emit_tool_calls:
            return
        await send_event_safe(tools, json.dumps(payload, default=str), message_type)


def images_from_result(result: ImageContentResult) -> list[Image]:
    """NOOA images for an image-file read, so the model can ``show()`` them."""
    return [
        cast(Image, Image.from_bytes(data, media_type=mime_type))
        for data, mime_type in decode_image_blocks(result)
    ]


def tool_method_doc(input_model: type[BaseModel]) -> str:
    """The docstring the model reads: the tool description plus its arguments."""
    return docstring_with_args(inspect.cleandoc(input_model.__doc__ or ""), input_model)


class ComputedDefault:
    """Stands in for a ``default_factory`` default in a rendered signature.

    Calling the factory just to document it could fail (a factory may take the
    validated data) or build an expensive object.
    """

    def __repr__(self) -> str:
        return "<computed>"


def documented_default(model_field: FieldInfo) -> object:
    """The default a signature shows for *model_field*."""
    if model_field.is_required():
        return inspect.Parameter.empty
    if model_field.default_factory is not None:
        return ComputedDefault()
    return model_field.default


def tool_method_signature(input_model: type[BaseModel]) -> inspect.Signature:
    """Keyword-only parameters mirroring the tool's input model."""
    parameters = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    for name, model_field in input_model.model_fields.items():
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=model_field.annotation,
                default=documented_default(model_field),
            )
        )
    return inspect.Signature(parameters, return_annotation=Any)


def tool_method(spec: RoomToolSpec) -> Callable[..., Awaitable[Any]]:
    """An async method that forwards its keyword arguments to the tool."""

    async def method(self: RoomTools, **arguments: Any) -> Any:
        return await self._invoke(spec.name, arguments)

    method.__name__ = spec.name
    method.__qualname__ = f"{RoomTools.__name__}.{spec.name}"
    method.__doc__ = tool_method_doc(spec.input_model)
    method.__signature__ = tool_method_signature(spec.input_model)  # type: ignore[attr-defined]
    return method


def room_tools_class(specs: list[RoomToolSpec]) -> type[RoomTools]:
    """A ``RoomTools`` subclass exposing exactly *specs* as methods."""
    namespace: dict[str, Any] = {spec.name: tool_method(spec) for spec in specs}
    namespace["_specs"] = {spec.name: spec for spec in specs}
    namespace["__doc__"] = RoomTools.__doc__
    namespace["__module__"] = RoomTools.__module__
    namespace["__qualname__"] = RoomTools.__qualname__
    return type(RoomTools.__name__, (RoomTools,), namespace)


def room_tools_reference(tools_class: type[RoomTools]) -> str:
    """Every tool's full signature and docstring, as the model reads them.

    NOOA's view of ``self`` shows only the first docstring line of a referenced
    type's methods, which would drop each tool's argument descriptions.
    """
    return doc(tools_class)


def platform_tool_spec(definition: ToolDefinition) -> RoomToolSpec:
    """Dispatch a Band tool through the turn's tools, as every adapter does.

    ``execute_tool_call_structured`` owns validation, dispatch and serialization,
    including any tools implementation that customizes dispatch.
    """

    async def run(tools: AgentToolsProtocol, arguments: dict[str, Any]) -> object:
        outcome = await tools.execute_tool_call_structured(definition.name, arguments)
        if not outcome.ok:
            raise BandToolError(outcome.error_message or str(outcome.value))
        return outcome.value

    return RoomToolSpec(definition.name, definition.input_model, run)


def custom_tool_spec(tool_def: CustomToolDef) -> RoomToolSpec:
    """Dispatch to a portable ``(InputModel, handler)`` custom tool."""
    input_model, _ = tool_def

    async def run(tools: AgentToolsProtocol, arguments: dict[str, Any]) -> object:
        return await execute_custom_tool(
            tool_def, by_alias(input_model, arguments), turn=tools.turn
        )

    return RoomToolSpec(get_custom_tool_name(input_model), input_model, run)


def by_alias(input_model: type[BaseModel], arguments: dict[str, Any]) -> dict[str, Any]:
    """Re-key keyword arguments from field names to the aliases validation expects.

    The model calls a tool with Python keyword arguments, which are always field
    names, while ``model_validate`` reads each field under its alias.
    """
    aliases = {
        name: model_field.alias
        for name, model_field in input_model.model_fields.items()
        if model_field.alias
    }
    return {aliases.get(key, key): value for key, value in arguments.items()}


def validate_custom_tool_names(custom_tools: list[CustomToolDef]) -> None:
    """Custom tools become ``RoomTools`` methods, so each name must be a public
    identifier that does not replace one of its own attributes."""
    names = [get_custom_tool_name(input_model) for input_model, _ in custom_tools]
    reject_conflicting_tool_names(names)
    if invalid := sorted(
        name
        for name in names
        if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_")
    ):
        raise ValueError(
            f"Custom tool names must be public Python identifiers: {invalid}"
        )
    if reserved := sorted(set(names) & set(dir(RoomTools))):
        raise ValueError(f"Custom tool names are reserved by RoomTools: {reserved}")
