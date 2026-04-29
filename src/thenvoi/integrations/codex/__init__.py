"""Codex app-server integration helpers."""

from __future__ import annotations

from .rpc_base import (
    CodexJsonRpcError,
    OverloadRetryPolicy,
    RpcEvent,
)
from .stdio_client import CodexStdioClient
from .types import (
    CODEX_APPROVAL_METHODS,
    CODEX_ERROR_REMEDIATION,
    ApprovalAuditEntry,
    CodexPlanStep,
    CodexSessionState,
    CodexTokenUsage,
    build_structured_error_metadata,
    parse_plan_steps,
)
from .websocket_client import CodexWebSocketClient

__all__ = [
    "CODEX_APPROVAL_METHODS",
    "CODEX_ERROR_REMEDIATION",
    "ApprovalAuditEntry",
    "CodexJsonRpcError",
    "CodexPlanStep",
    "CodexSdkClient",
    "CodexSessionState",
    "CodexStdioClient",
    "CodexTokenUsage",
    "CodexWebSocketClient",
    "OverloadRetryPolicy",
    "RpcEvent",
    "build_structured_error_metadata",
    "parse_plan_steps",
]


def __getattr__(name: str) -> object:
    if name == "CodexSdkClient":
        from .sdk_client import CodexSdkClient

        return CodexSdkClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
