"""Persistent subprocess transport for OMP's native JSONL RPC protocol."""

from __future__ import annotations

from band.integrations.omprpc.client import OmpRpcClient
from band.integrations.omprpc.errors import (
    OmpProcessError,
    OmpPromptError,
    OmpProtocolError,
    OmpRpcError,
)

__all__ = [
    "OmpProcessError",
    "OmpPromptError",
    "OmpProtocolError",
    "OmpRpcClient",
    "OmpRpcError",
]
