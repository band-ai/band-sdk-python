"""The ``self.room`` tool surface a NOOA model calls from its generated code.

Each Band tool, and each portable custom tool, becomes an async method on a
``RoomTools`` subclass. Platform methods expose named parameters; custom
methods receive the original Pydantic validation payload. A call works only
while the turn that made it is running.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import keyword
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast
from uuid import uuid4

from nooa import Image, hidden
from nooa.agentdoc import doc
from nooa.agentdoc.ext import format_type
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
from band.runtime.working_state import tool_activity

logger = logging.getLogger(__name__)

ToolRunner = Callable[[AgentToolsProtocol, dict[str, Any]], Awaitable[object]]

INPUT_CONFIG_KEYS = (
    "populate_by_name",
    "validate_by_alias",
    "validate_by_name",
    "strict",
    "extra",
)


@dataclass(frozen=True)
class RoomToolSpec:
    """One tool the model can call as ``await self.room.<name>(...)``."""

    name: str
    run: ToolRunner
    method: Callable[..., Awaitable[Any]]
    reference_types: tuple[type[BaseModel], ...] = ()


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
        async with tool_activity(tools, name):
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
                value = serialize_tool_result(
                    await self._specs[name].run(tools, arguments)
                )
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


def tool_method(
    name: str, input_model: type[BaseModel]
) -> Callable[..., Awaitable[Any]]:
    """An async method that forwards its keyword arguments to the tool."""

    async def method(self: RoomTools, **arguments: Any) -> Any:
        return await self._invoke(name, arguments)

    describe_method(method, name, tool_method_doc(input_model))
    method.__signature__ = tool_method_signature(input_model)  # type: ignore[attr-defined]
    return method


def describe_method(
    method: Callable[..., Awaitable[Any]], name: str, description: str
) -> None:
    method.__name__ = name
    method.__qualname__ = f"{RoomTools.__name__}.{name}"
    method.__doc__ = description


def custom_field_doc(name: str, model_field: FieldInfo) -> str:
    """Document field metadata without claiming to describe a validation grammar."""
    requirement = (
        "required"
        if model_field.is_required()
        else f"default={documented_default(model_field)!r}"
    )
    return (
        f"{name}: {format_type(model_field.annotation)}; "
        f"validation_alias={model_field.validation_alias!r}; {requirement}. "
        f"{model_field.description or ''}"
    )


def custom_tool_method(
    name: str, input_model: type[BaseModel]
) -> Callable[..., Awaitable[Any]]:
    """Expose the original validation payload without reinterpreting its keys."""

    async def method(self: RoomTools, *, arguments: dict[str, Any]) -> Any:
        return await self._invoke(name, arguments)

    fields = "\n".join(
        custom_field_doc(field_name, model_field)
        for field_name, model_field in input_model.model_fields.items()
    )
    # Generated aliases are resolved on each field; callable config adds no input keys.
    input_config = {
        key: input_model.model_config.get(key)
        for key in INPUT_CONFIG_KEYS
        if key in input_model.model_config
    }
    describe_method(
        method,
        name,
        f"{inspect.cleandoc(input_model.__doc__ or '')}\n\n"
        "Pass arguments as the original Pydantic validation payload, unchanged.\n"
        f"Model fields (attributes, not method keyword arguments):\n{fields}\n"
        "AliasChoices are ordered; AliasPath describes a nested payload.\n"
        f"Input configuration: {input_config!r}\n"
        "By default use the validation alias when present, otherwise the attribute name.\n"
        "Model configuration and validators may impose different payload rules.",
    )
    return method


def room_tools_class(specs: list[RoomToolSpec]) -> type[RoomTools]:
    """A ``RoomTools`` subclass exposing exactly *specs* as methods."""
    namespace: dict[str, Any] = {spec.name: spec.method for spec in specs}
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
    references = dict.fromkeys(
        model for spec in tools_class._specs.values() for model in spec.reference_types
    )
    # NOOA's multi-object renderer truncates referenced types; render each root separately.
    sections = [doc(tools_class)]
    sections.extend(doc(model, inline_depth=sys.maxsize) for model in references)
    return "\n\n".join(sections)


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

    return RoomToolSpec(
        definition.name, run, tool_method(definition.name, definition.input_model)
    )


def custom_tool_spec(tool_def: CustomToolDef) -> RoomToolSpec:
    """Dispatch to a portable ``(InputModel, handler)`` custom tool."""
    input_model, _ = tool_def

    async def run(tools: AgentToolsProtocol, arguments: dict[str, Any]) -> object:
        return await execute_custom_tool(tool_def, arguments, turn=tools.turn)

    name = get_custom_tool_name(input_model)
    return RoomToolSpec(
        name, run, custom_tool_method(name, input_model), (input_model,)
    )


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
