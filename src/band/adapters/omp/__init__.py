"""Native OMP adapter public API."""

from __future__ import annotations

from band.adapters.omp.adapter import OmpAdapter as OmpAdapter
from band.adapters.omp.config import McpServerResolver as McpServerResolver
from band.adapters.omp.config import OmpAdapterConfig as OmpAdapterConfig
from band.adapters.omp.config import OmpPermissionRequest as OmpPermissionRequest
from band.adapters.omp.roomstate import OMP_SESSION_DIRECTORY as OMP_SESSION_DIRECTORY
from band.adapters.omp.toolprotocol import (
    OMP_PERMISSION_CALL_ID_PREFIX as OMP_PERMISSION_CALL_ID_PREFIX,
)
from band.adapters.omp.toolprotocol import (
    OMP_PERMISSION_TOOL as OMP_PERMISSION_TOOL,
)
from band.adapters.omp.toolprotocol import (
    OMP_TOOL_APPROVAL_HEADER as OMP_TOOL_APPROVAL_HEADER,
)

__all__ = [
    "OMP_PERMISSION_CALL_ID_PREFIX",
    "OMP_PERMISSION_TOOL",
    "OMP_SESSION_DIRECTORY",
    "OMP_TOOL_APPROVAL_HEADER",
    "McpServerResolver",
    "OmpAdapter",
    "OmpAdapterConfig",
    "OmpPermissionRequest",
]
