"""Declare the shared dependencies of the OMP adapter concern mixins."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from pydantic import JsonValue

from band.adapters.omp.config import (
    McpServerResolver,
    OmpAdapterConfig,
    PermissionResolver,
)
from band.adapters.omp.roomstate import OmpRoom, RoomGate
from band.adapters.omp.turnstate import OmpTurn
from band.converters.omp import OmpSessionState
from band.core.model_catalog import ModelSelection
from band.core.protocols import AgentToolsProtocol
from band.core.retention import RoomRetention
from band.core.types import AdapterFeatures, Capability, PlatformMessage
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import RoomWorkspaces


class OmpAdapterContract(ABC):
    config: OmpAdapterConfig
    features: AdapterFeatures
    agent_name: str | None
    agent_description: str | None
    _custom_tools: list[CustomToolDef]
    _resolve_permission: PermissionResolver | None
    _resolve_mcp_servers: McpServerResolver | None
    _workspaces: RoomWorkspaces
    _rooms: dict[str, OmpRoom]
    _locks: dict[str, RoomGate]
    _selection_locks: dict[str, asyncio.Lock]
    _room_selections: dict[str, ModelSelection]
    _retention: RoomRetention

    @property
    @abstractmethod
    def model_selection(self) -> ModelSelection: ...

    @abstractmethod
    def _host_tools(self) -> list[dict[str, JsonValue]]: ...

    @abstractmethod
    def _host_tool_capabilities(self) -> frozenset[Capability]: ...

    @abstractmethod
    def _spawn_model(self, selection: ModelSelection | None) -> str | None: ...

    @abstractmethod
    def _spawn_env(
        self, selection: ModelSelection | None = None
    ) -> dict[str, str] | None: ...

    @abstractmethod
    def _spawn_command(
        self, workspace: str | None, selection: ModelSelection | None = None
    ) -> list[str]: ...

    @abstractmethod
    def _build_system_context(self) -> str: ...

    @abstractmethod
    def _room_gate(self, room_id: str) -> RoomGate: ...

    @abstractmethod
    def _room(self, room_id: str) -> OmpRoom: ...

    @abstractmethod
    async def _prepare_room(self, room: OmpRoom, history: OmpSessionState) -> None: ...

    @abstractmethod
    async def _refresh_state(self, room: OmpRoom) -> None: ...

    @abstractmethod
    def _request_disposal(self, room: OmpRoom) -> asyncio.Task[None] | None: ...

    @abstractmethod
    async def _join_disposal(self, room: OmpRoom) -> None: ...

    @abstractmethod
    async def _emit_event(self, event: OmpEvent, turn: OmpTurn) -> None: ...

    @abstractmethod
    async def _execute_tool(
        self, call: OmpHostToolCall, tools: AgentToolsProtocol
    ) -> OmpToolResult: ...

    @abstractmethod
    async def _ask_room(
        self,
        request: OmpUIRequest,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> OmpUIResponse: ...

    @abstractmethod
    async def _permission(
        self, request: OmpUIRequest, room_id: str, session_id: str, turn: OmpTurn
    ) -> OmpUIResponse: ...

    @abstractmethod
    def _project_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        preserve_native_semantics: bool = False,
    ) -> tuple[str, dict[str, JsonValue]]: ...

    @abstractmethod
    def _encode_tool_call(
        self, name: str, arguments: dict[str, JsonValue], call_id: str
    ) -> str: ...

    @abstractmethod
    def _encode_tool_result(
        self, name: str, output: str, call_id: str | None, *, is_error: bool
    ) -> str: ...

    @abstractmethod
    def _session_task_metadata(
        self, room_id: str, room: OmpRoom
    ) -> dict[str, JsonValue]: ...

    @abstractmethod
    async def apply_model_selection(
        self, selection: ModelSelection, *, room_id: str
    ) -> None: ...

    @abstractmethod
    async def on_cleanup(self, room_id: str) -> None: ...

    @abstractmethod
    async def cleanup_all(self) -> None: ...
