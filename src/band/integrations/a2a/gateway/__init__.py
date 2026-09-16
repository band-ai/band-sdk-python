"""A2A Gateway adapter for exposing Band peers as A2A endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.exports import lazy_exports

if TYPE_CHECKING:
    from band.integrations.a2a.gateway.adapter import (
        A2AGatewayAdapter as A2AGatewayAdapter,
    )
    from band.integrations.a2a.gateway.config import (
        A2AGatewayAdapterConfig as A2AGatewayAdapterConfig,
    )
    from band.integrations.a2a.gateway.server import GatewayServer as GatewayServer
    from band.integrations.a2a.gateway.types import (
        GatewaySessionState as GatewaySessionState,
        PendingA2ATask as PendingA2ATask,
    )

__all__, __getattr__ = lazy_exports(
    __name__,
    adapter=["A2AGatewayAdapter"],
    config=["A2AGatewayAdapterConfig"],
    server=["GatewayServer"],
    types=["GatewaySessionState", "PendingA2ATask"],
)
