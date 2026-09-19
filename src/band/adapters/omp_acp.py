"""Oh My P.I. adapter over ACP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import ACPClientAdapter, PermissionResolver
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef

DEFAULT_OMP_ACP_COMMAND: tuple[str, ...] = ("omp", "acp")
_UNSAFE_APPROVAL_ARGUMENTS = frozenset({"--yolo", "--auto-approve"})


@dataclass(frozen=True)
class OmpACPAdapterConfig:
    """Runtime configuration for an ``omp acp`` stdio backend."""

    command: tuple[str, ...] = DEFAULT_OMP_ACP_COMMAND
    cwd: str | None = None
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None
    resolve_permission: PermissionResolver | None = None


class OmpACPAdapter(ACPClientAdapter):
    """Band adapter for Oh My P.I.'s native ACP stdio server."""

    def __init__(
        self,
        config: OmpACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        config = config or OmpACPAdapterConfig()
        if _uses_unsafe_approval(config.command):
            raise ValueError(
                "OMP auto-approve modes bypass Band permission resolution; "
                "use an approval-gated mode instead"
            )
        super().__init__(
            command=list(config.command),
            cwd=config.cwd,
            env=config.env,
            custom_section=config.custom_section,
            inject_band_tools=config.inject_band_tools,
            mcp_servers=config.mcp_servers,
            additional_tools=additional_tools,
            resolve_session_config=config.resolve_session_config,
            resolve_permission=config.resolve_permission,
            **features,
        )


def _uses_unsafe_approval(command: tuple[str, ...]) -> bool:
    """Whether an OMP command disables permission-gated tool execution."""
    return bool(_UNSAFE_APPROVAL_ARGUMENTS.intersection(command)) or any(
        option == "--approval-mode" and value == "yolo"
        for option, value in zip(command, command[1:])
    )


__all__ = ["DEFAULT_OMP_ACP_COMMAND", "OmpACPAdapter", "OmpACPAdapterConfig"]
