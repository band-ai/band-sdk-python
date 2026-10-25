"""Configure the native OMP adapter and its advertised host tools."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import JsonValue
from typing_extensions import Unpack

from band.adapters.omp.callbacks import OmpCallbackMixin
from band.adapters.omp.config import (
    McpServerResolver,
    OmpAdapterConfig,
    PermissionResolver,
)
from band.adapters.omp.events import OmpEventMixin
from band.adapters.omp.lifecycle import OmpRoomLifecycleMixin
from band.adapters.omp.roomstate import OmpRoom, RoomGate
from band.adapters.omp.toolprotocol import _omp_host_tool
from band.adapters.omp.turns import OmpTurnMixin
from band.converters.omp import OmpHistoryConverter, OmpSessionState
from band.core.model_catalog import ModelSelection
from band.core.retention import RoomRetention, RoomRetentionConfig
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, Capability, Emit, FeatureKwargs
from band.integrations.omp import finalize_omp_command
from band.integrations.ompprocess import omp_rpc_command_in_workspace, omp_runtime_env
from band.runtime.custom_tools import CustomToolDef, custom_tools_to_schemas
from band.runtime.tools import (
    get_band_tool_category,
    get_tool_description,
    iter_tool_definitions,
    platform_args_schema,
)
from band.workspaces import RoomWorkspaces, WorkspaceResolver, workspace_resolver_for


class OmpAdapter(
    OmpRoomLifecycleMixin,
    OmpTurnMixin,
    OmpCallbackMixin,
    OmpEventMixin,
    SimpleAdapter[OmpSessionState],
):
    """Keep Band's turn owned until OMP reports native session settlement.

    Host tools capture the current turn's AgentTools, never a mutable room-wide
    pointer. Cancellation retires the entire process before another turn starts.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.TASK_EVENTS}
    )

    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: OmpAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        resolve_permission: PermissionResolver | None = None,
        resolve_mcp_servers: McpServerResolver | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        super().__init__(history_converter=OmpHistoryConverter(), **features)
        self.config = config or OmpAdapterConfig()
        self._custom_tools = list(additional_tools or [])
        self._resolve_permission = resolve_permission
        self._workspaces = RoomWorkspaces(
            workspace_resolver_for(self.config.cwd, workspace_for_room)
        )
        self._rooms: dict[str, OmpRoom] = {}
        self._locks: dict[str, RoomGate] = {}
        self._selection_locks: dict[str, asyncio.Lock] = {}
        self._room_selections: dict[str, ModelSelection] = {}
        self._retention = RoomRetention(
            RoomRetentionConfig(transition_timeout_s=self.config.turn_timeout_s)
        )
        self._resolve_mcp_servers = resolve_mcp_servers
        self._host_schemas = self._build_host_tools()

    def apply_effective_features(self, features: AdapterFeatures) -> None:
        super().apply_effective_features(features)
        self._host_schemas = self._build_host_tools()

    @property
    def model_selection(self) -> ModelSelection:
        return ModelSelection(
            model=self.config.model, reasoning_effort=self.config.reasoning_effort
        )

    def _host_tools(self) -> list[dict[str, JsonValue]]:
        return self._host_schemas

    def _host_tool_capabilities(self) -> frozenset[Capability]:
        return self.features.capabilities | {Capability.CONTACTS}

    def _build_host_tools(self) -> list[dict[str, JsonValue]]:
        definitions = list(
            iter_tool_definitions(capabilities=self._host_tool_capabilities())
        )
        definitions = filter_tool_schemas(
            definitions,
            self.features,
            get_name=lambda item: item.name,
            get_category=lambda item: get_band_tool_category(item.name),
        )
        tools: list[dict[str, JsonValue]] = []
        if self.config.inject_band_tools:
            tools.extend(
                _omp_host_tool(
                    item.name,
                    get_tool_description(item.name),
                    platform_args_schema(item.name).model_json_schema(),
                )
                for item in definitions
            )
        for schema in custom_tools_to_schemas(self._custom_tools, "openai"):
            function = schema["function"]
            tools.append(
                _omp_host_tool(
                    function["name"],
                    function.get("description", ""),
                    function["parameters"],
                )
            )
        names = [tool["name"] for tool in tools]
        if len(set(names)) != len(names):
            raise ValueError("OMP host tools must have unique names")
        return tools

    def _spawn_model(self, selection: ModelSelection | None) -> str | None:
        if selection is not None and selection.model:
            return selection.model
        return self.config.model

    def _spawn_env(
        self, selection: ModelSelection | None = None
    ) -> dict[str, str] | None:
        return omp_runtime_env(
            env=self.config.env,
            model=self._spawn_model(selection),
            api_key=self.config.api_key,
        )

    def _spawn_command(
        self, workspace: str | None, selection: ModelSelection | None = None
    ) -> list[str]:
        command = finalize_omp_command(
            self.config.command,
            model=self._spawn_model(selection),
            approval_mode=self.config.approval_mode,
        )
        return (
            omp_rpc_command_in_workspace(command, workspace) if workspace else command
        )
