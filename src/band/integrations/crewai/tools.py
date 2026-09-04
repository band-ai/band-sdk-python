"""Shared CrewAI BaseTool wrappers for Band platform tools.

Both CrewAIAdapter and CrewAIFlowAdapter consume the same tool builder so that
the platform tool surface stays consistent across adapters and Flow authors who
spawn sub-Crews inside @listen methods get platform tools without copying code.

The builder takes three injectables:
- get_context: callable returning the current room context (room_id + tools).
  Each adapter owns its own ContextVar and supplies its own getter.
- reporter: CrewAIToolReporter implementation. Two ship in this integration:
  EmitToolCallsReporter (gates by Emit.TOOL_CALLS) and NoopReporter.
- capabilities: frozenset[Capability] — controls which tool subset is exposed.

What each tool *does* lives in ``catalog.py``; this module is how one is run
and handed to a crew. It is also the integration's public face: the names in
``__all__`` are re-exported here for both adapters and their tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

if TYPE_CHECKING:
    from crewai.tools import BaseTool

from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, Capability
from band.integrations.crewai.catalog import (
    PLATFORM_TOOLS,
    Invocation,
    ToolSpec,
    serialize_success_result,
    vision_sentinel,
)
from band.integrations.crewai.reporting import (
    CrewAIToolContext,
    CrewAIToolReporter,
    EmitToolCallsReporter,
    NoopReporter,
    ReplyTracker,
)
from band.integrations.crewai.runtime import run_async
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
    is_marked_terminal,
)
from band.runtime.tools import (
    CAPABILITY_TOOL_NAMES,
    EVENT_TOOL_NAMES,
    BandTool,
    append_available_mention_handles,
    get_band_tool_category,
    get_tool_description,
    is_terminal_success,
)

logger = logging.getLogger(__name__)


# --- Execution ---


def _execute_tool(
    *,
    tool_name: str,
    coro_factory: Callable[[AgentToolsProtocol], Any],
    get_context: Callable[[], CrewAIToolContext | None],
    reporter: CrewAIToolReporter,
    fallback_loop: asyncio.AbstractEventLoop | None,
    custom_terminal: bool = False,
) -> str:
    """Execute a tool with common error handling and reporting.

    Returns a JSON string with status and result/error.
    """
    context = get_context()
    if context is None:
        return json.dumps(
            {
                "status": "error",
                "message": "No room context available - tool called outside message handling",
            }
        )

    room_id = context.room_id
    tools = context.tools

    async def _execute() -> str:
        try:
            return await coro_factory(tools)
        except Exception as e:
            error_msg = str(e)
            if tool_name == BandTool.SEND_MESSAGE and isinstance(
                e, (ValueError, BandToolError)
            ):
                error_msg = append_available_mention_handles(
                    error_msg,
                    tools.participants,
                    getattr(tools, "agent_id", None),
                )
            logger.error("%s failed in room %s: %s", tool_name, room_id, error_msg)
            if tool_name not in EVENT_TOOL_NAMES:
                await reporter.report_result(tools, tool_name, error_msg, is_error=True)
            return json.dumps({"status": "error", "message": error_msg})

    result = run_async(_execute(), fallback_loop=fallback_loop)

    if context.reply_tracker is not None:
        _mark_productive_work(
            context.reply_tracker,
            tool_name,
            result,
            custom_terminal=custom_terminal,
        )
    return result


def _mark_productive_work(
    tracker: ReplyTracker, tool_name: str, result: str, *, custom_terminal: bool
) -> None:
    """Record that the turn did real work, so an empty final answer stays benign.

    CrewAI raises on an empty final answer; that is a genuine no-response
    failure only when nothing terminal ran. ``is_terminal_success`` is the
    shared rule for what counts (read-only Band tools and undeclared custom
    tools do not).
    """
    try:
        if json.loads(result).get("status") != "success":
            return
    except (json.JSONDecodeError, AttributeError, TypeError):
        return
    if is_terminal_success(tool_name, succeeded=True, custom_terminal=custom_terminal):
        tracker.tool_executed = True
    if tool_name == BandTool.SEND_MESSAGE:
        tracker.replied = True


# --- Tool factory ---

_no_cache: Any = staticmethod(lambda *_a, **_kw: False)


def _platform_tool(
    spec: ToolSpec,
    *,
    get_context: Callable[[], CrewAIToolContext | None],
    reporter: CrewAIToolReporter,
    fallback_loop: asyncio.AbstractEventLoop | None,
) -> BaseTool:
    """Wrap one ToolSpec as the CrewAI BaseTool instance the crew is handed."""
    from crewai.tools import BaseTool  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv

    class PlatformTool(BaseTool):
        # str(...): pydantic doesn't validate field defaults (no
        # validate_default here), so an unwrapped BandTool (StrEnum) default
        # would leave tool.name a BandTool instance at runtime, not the str
        # the field is typed as.
        name: str = str(spec.name)
        description: str = get_tool_description(spec.name)
        args_schema: type[BaseModel] = spec.args_schema
        cache_function: Any = _no_cache

        def _run(self, *_args: Any, **kwargs: Any) -> Any:
            return _execute_tool(
                tool_name=spec.name,
                coro_factory=lambda tools: spec.invoke(
                    Invocation(tools=tools, reporter=reporter), kwargs
                ),
                get_context=get_context,
                reporter=reporter,
                fallback_loop=fallback_loop,
            )

    return PlatformTool()


def _custom_tool(
    definition: CustomToolDef,
    *,
    get_context: Callable[[], CrewAIToolContext | None],
    reporter: CrewAIToolReporter,
    fallback_loop: asyncio.AbstractEventLoop | None,
) -> BaseTool:
    """Wrap one CustomToolDef as a CrewAI BaseTool instance."""
    from crewai.tools import BaseTool  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv

    input_model, handler = definition
    tool_name = get_custom_tool_name(input_model)
    # Only a custom tool that opts in (band_terminal=True) lets an empty final
    # answer be treated as benign; undeclared customs fail loud.
    terminal = is_marked_terminal(handler)

    class CustomCrewAITool(BaseTool):
        name: str = tool_name
        description: str = input_model.__doc__ or f"Execute {tool_name}"
        args_schema: type[BaseModel] = input_model
        cache_function: Any = _no_cache

        def _run(self, *_args: Any, **kwargs: Any) -> Any:
            async def execute(tools: AgentToolsProtocol) -> str:
                await reporter.report_call(tools, tool_name, kwargs)
                result = await execute_custom_tool(definition, kwargs)
                await reporter.report_result(tools, tool_name, result)
                return json.dumps({"status": "success", "result": result}, default=str)

            return _execute_tool(
                tool_name=tool_name,
                coro_factory=execute,
                get_context=get_context,
                reporter=reporter,
                fallback_loop=fallback_loop,
                custom_terminal=terminal,
            )

    return CustomCrewAITool()


def _enabled_specs(capabilities: frozenset[Capability]) -> list[ToolSpec]:
    """The platform tools a crew with these capabilities is allowed to see."""
    withheld: frozenset[str] = frozenset().union(
        *(
            names
            for capability, names in CAPABILITY_TOOL_NAMES.items()
            if capability not in capabilities
        )
    )
    return [spec for spec in PLATFORM_TOOLS if spec.name not in withheld]


def build_band_crewai_tools(
    *,
    get_context: Callable[[], CrewAIToolContext | None],
    reporter: CrewAIToolReporter,
    capabilities: frozenset[Capability] = frozenset(),
    features: AdapterFeatures | None = None,
    custom_tools: list[CustomToolDef] | None = None,
    fallback_loop: asyncio.AbstractEventLoop | None = None,
) -> list[BaseTool]:
    """Build the CrewAI BaseTool instances for the platform tool surface.

    Chat tools are always present; contact, memory and file tools follow their
    capability, and custom tools are appended after the platform ones. The
    returned tools close over ``get_context``, ``reporter`` and
    ``fallback_loop``, so each adapter supplies its own and the wrappers stay
    framework-agnostic.
    """
    active_features = features or AdapterFeatures(capabilities=capabilities)
    selected: list[BaseTool] = [
        _platform_tool(
            spec,
            get_context=get_context,
            reporter=reporter,
            fallback_loop=fallback_loop,
        )
        for spec in _enabled_specs(active_features.capabilities)
    ]

    selected = filter_tool_schemas(
        selected,
        active_features,
        get_name=lambda tool: tool.name,
        get_category=lambda tool: get_band_tool_category(tool.name),
    )

    selected.extend(
        _custom_tool(
            definition,
            get_context=get_context,
            reporter=reporter,
            fallback_loop=fallback_loop,
        )
        for definition in custom_tools or ()
    )
    return selected


__all__ = [
    "CrewAIToolContext",
    "CrewAIToolReporter",
    "EmitToolCallsReporter",
    "NoopReporter",
    "ReplyTracker",
    "build_band_crewai_tools",
    "serialize_success_result",
    "vision_sentinel",
]
