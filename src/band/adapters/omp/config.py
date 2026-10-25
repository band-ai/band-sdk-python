"""Validate OMP launch configuration and resolver inputs."""

from __future__ import annotations

import collections.abc
from collections.abc import Awaitable, Callable
from typing import Literal, Self

from pydantic import BaseModel, Field, PositiveFloat, field_validator, model_validator

from band.core.adapterconfig import BaseAdapterConfig
from band.integrations.omp import OMP_APPROVAL_MODE_ALWAYS_ASK, validate_omp_command
from band.integrations.ompprocess import DEFAULT_OMP_COMMAND, OmpMcpServer


class OmpAdapterConfig(BaseAdapterConfig):
    """Native launch settings; each room owns a process and persistent session."""

    command: tuple[str, ...] = DEFAULT_OMP_COMMAND
    env: dict[str, str] | None = Field(default=None, repr=False)
    cwd: str | None = None
    mcp_servers: dict[str, OmpMcpServer] = Field(default_factory=dict)
    inject_band_tools: bool = True
    custom_section: str = ""
    turn_timeout_s: PositiveFloat = 300.0
    model: str | None = None
    reasoning_effort: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    approval_mode: Literal["always-ask", "yolo"] = OMP_APPROVAL_MODE_ALWAYS_ASK

    @field_validator("command")
    @classmethod
    def _check_command(cls, command: tuple[str, ...]) -> tuple[str, ...]:
        if not command:
            raise ValueError("OMP RPC requires a command")
        validate_omp_command(command)
        return command

    @model_validator(mode="after")
    def _check_credentials(self) -> Self:
        if self.api_key is not None and self.model is None:
            raise ValueError(
                "api_key needs model to select its provider environment variable"
            )
        return self


class OmpPermissionRequest(BaseModel):
    room_id: str
    session_id: str
    title: str
    options: tuple[str, ...]


PermissionResolver = Callable[[OmpPermissionRequest], Awaitable[str | None]]

McpServerResolver = Callable[
    [str], Awaitable[collections.abc.Mapping[str, OmpMcpServer]]
]
