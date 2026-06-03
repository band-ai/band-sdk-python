"""A2A Gateway adapter for exposing Band peers as A2A endpoints."""

from band.integrations.a2a.gateway.adapter import A2AGatewayAdapter
from band.integrations.a2a.gateway.server import GatewayServer
from band.integrations.a2a.gateway.types import GatewaySessionState, PendingA2ATask

__all__ = [
    "A2AGatewayAdapter",
    "GatewayServer",
    "GatewaySessionState",
    "PendingA2ATask",
]
