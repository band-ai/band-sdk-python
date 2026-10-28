"""Connect rooms to Google's standalone Antigravity ACP server.

Install the server separately and use the SDK's ``acp`` extra. Each agent/room
gets a persistent private ``GEMINI_HOME``; cleanup stops its process but keeps
that native state for resume. Parent provider credentials are not inherited:
pass ``gemini_api_key`` or explicit ``env`` entries. An explicit environment
value takes precedence over the convenience credential setting.

``auto_accept`` grants requested tools once, including the host user's shell,
files, browser and web access. ``auto_decline`` declines requested tools on
external MCP servers too; Band and additional tools remain available during
their active room turn. Native subagent permission requests are declined.
The native scheduler and some file reads bypass ACP permissions: this policy
does not sandbox the server. Idle retention remains unavailable.

Band memory is separate from native Antigravity state. Homes must be private
on shared hosts; a custom Windows home root must have appropriate user ACLs.
Antigravity's live MCP results can contain a label instead of the result text.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path, PureWindowsPath
from typing import ClassVar

from pydantic import Field, field_validator
from typing_extensions import Unpack

from band.core.types import ApprovalMode, FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    ACPPermissionRequest,
    McpServerResolver,
    PermissionResolver,
)
from band.integrations.acp.client_types import ACPRoom
from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.permissions import (
    ALLOW_ONCE_KIND,
    REJECT_ONCE_KIND,
    option_id_of_kind,
)
from band.integrations.acp.session_config import SessionConfigResolver
from band.integrations.acp.types import ACPToolCall
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from band.workspaces import WorkspaceResolver, create_room_workspace_resolver

logger = logging.getLogger(__name__)

ANTIGRAVITY_EXECUTABLE = "agy_acp_server.par"
ANTIGRAVITY_WINDOWS_EXECUTABLE = "agy_acp_server.exe"
ANTIGRAVITY_LINUX_UID_ARGUMENT = "--uid="
TESTED_ANTIGRAVITY_VERSION = "1.4.3"
DEFAULT_ANTIGRAVITY_HOME_ROOT = "~/.band/antigravity-acp"
PRIVATE_HOME_MODE = 0o700
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_HOME_ENV = "GEMINI_HOME"
INVOKE_SUBAGENT_TITLE = "Run invoke_subagent?"
BACKGROUND_BUILTIN_TITLES = frozenset({INVOKE_SUBAGENT_TITLE})
ROOM_DELIVERY_GUIDANCE = (
    f"Only {BandTool.SEND_MESSAGE} delivers a reply to this Band room. "
    "Antigravity's native send_message and inbox do not reach the room."
)


def default_antigravity_command(platform: str) -> tuple[str, ...]:
    """Select the standalone server command for ``platform``."""
    match platform:
        case "linux":
            return ANTIGRAVITY_EXECUTABLE, ANTIGRAVITY_LINUX_UID_ARGUMENT
        case "win32":
            return (ANTIGRAVITY_WINDOWS_EXECUTABLE,)
        case _:
            return (ANTIGRAVITY_EXECUTABLE,)


class AntigravityACPAdapterConfig(ACPClientAdapterConfig):
    """Configure the Antigravity server and its persistent private home root."""

    retention_unavailable_reason: ClassVar[str] = (
        "Antigravity's native scheduler, subagents and inbox are not qualified "
        "for safe idle suspension and resume"
    )

    command: tuple[str, ...] = default_antigravity_command(sys.platform)
    auth_method: str | None = "gemini-api-key"
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_home: str = Field(default=DEFAULT_ANTIGRAVITY_HOME_ROOT, min_length=1)
    approval_mode: ApprovalMode = "auto_accept"

    @field_validator("env")
    @classmethod
    def validate_owned_home(cls, env: dict[str, str] | None) -> dict[str, str] | None:
        if env is not None and any(
            name.casefold() == GEMINI_HOME_ENV.casefold() for name in env
        ):
            raise ValueError("set gemini_home instead of env.GEMINI_HOME")
        return env

    @field_validator("approval_mode")
    @classmethod
    def validate_approval_mode(cls, mode: ApprovalMode) -> ApprovalMode:
        if mode == "manual":
            raise ValueError("approval_mode supports auto_accept or auto_decline")
        return mode


class AntigravityToolObserver:
    """Warn once when one server process sends unsupported MCP metadata."""

    def __init__(self) -> None:
        self._warned = False

    def __call__(self, call: ACPToolCall) -> None:
        if call.mcp_metadata_rejected and not self._warned:
            self._warned = True
            logger.warning(
                "Antigravity sent an unrecognized MCP call shape; "
                "the tested ACP server version is %s",
                TESTED_ANTIGRAVITY_VERSION,
            )


class AntigravityACPAdapter(ACPClientAdapter[AntigravityACPAdapterConfig]):
    """Run an Antigravity ACP process with isolated state for each room."""

    def __init__(
        self,
        config: AntigravityACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        resolve_session_config: SessionConfigResolver | None = None,
        resolve_permission: PermissionResolver | None = None,
        resolve_mcp_servers: McpServerResolver | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Bridge rooms using the backend's permission policy unless overridden."""
        super().__init__(
            config or AntigravityACPAdapterConfig(),
            additional_tools=additional_tools,
            workspace_for_room=workspace_for_room,
            resolve_session_config=resolve_session_config,
            resolve_permission=(
                self._resolve_antigravity_permission
                if resolve_permission is None
                else resolve_permission
            ),
            resolve_mcp_servers=resolve_mcp_servers,
            **features,
        )

    def _credential_env(self) -> dict[str, str]:
        key = self.config.gemini_api_key
        return {GEMINI_API_KEY_ENV: key} if key else {}

    def _spawn_env(self, room_id: str) -> dict[str, str]:
        agent_id = self.require_platform().agent_id
        root = self._private_home(Path(self.config.gemini_home).expanduser().resolve())
        agent_home = self._private_home(root, agent_id)
        room_home = self._private_home(agent_home, room_id)
        return {**(super()._spawn_env(room_id) or {}), GEMINI_HOME_ENV: str(room_home)}

    @staticmethod
    def _private_home(root: Path, identity: str | None = None) -> Path:
        if identity is not None and (
            identity in {"", ".", ".."}
            or Path(identity).name != identity
            or PureWindowsPath(identity).name != identity
        ):
            raise ValueError("Antigravity identity must be one path component")
        path = (
            root
            if identity is None
            else Path(create_room_workspace_resolver(root)(identity))
        )
        if identity is not None and (path.parent != root or path.name != identity):
            raise ValueError("Antigravity home cannot alias another identity")
        path.mkdir(mode=PRIVATE_HOME_MODE, parents=True, exist_ok=True)
        if os.name != "nt":
            path.chmod(PRIVATE_HOME_MODE)
        return path

    def _runtime_client_factory(
        self, room: ACPRoom, generation: int
    ) -> ACPCollectingClient:
        return ACPCollectingClient(
            profile=self._client_profile(room, generation),
            canonicalize_tool_name=self._canonical_tool_name,
            observe_tool_call=AntigravityToolObserver(),
        )

    def _build_system_context(self, room_id: str) -> str:
        return f"{super()._build_system_context(room_id)}\n\n{ROOM_DELIVERY_GUIDANCE}"

    def _permission_turn_is_active(self, request: ACPPermissionRequest) -> bool:
        handle = self.backend_turns.active_handle(request.room_id)
        room = self._rooms.get(request.room_id)
        return (
            handle is not None
            and handle.session_id == request.session_id
            and room is not None
            and room.live_session == (request.session_id, handle.runtime)
            and room.retained.has_authority(room.generation)
        )

    async def _resolve_antigravity_permission(
        self, request: ACPPermissionRequest
    ) -> str | None:
        call = request.tool_call
        if (
            call.mcp_server == BAND_MCP_SERVER_NAME
            and call.name in self._own_tool_names
        ):
            kind = (
                ALLOW_ONCE_KIND
                if self._permission_turn_is_active(request)
                else REJECT_ONCE_KIND
            )
        elif call.mcp_server is None and call.title in BACKGROUND_BUILTIN_TITLES:
            kind = REJECT_ONCE_KIND
        else:
            kind = (
                ALLOW_ONCE_KIND
                if self.config.approval_mode == "auto_accept"
                else REJECT_ONCE_KIND
            )
            logger.info(
                "Antigravity tool permission: %s",
                kind,
            )
        return option_id_of_kind(request.options, kind)


__all__ = [
    "AntigravityACPAdapter",
    "AntigravityACPAdapterConfig",
    "default_antigravity_command",
]
