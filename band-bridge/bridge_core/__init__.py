"""Public API for band-bridge."""

from __future__ import annotations

from .bridge import AgentRunner, BandBridge, main
from .config import (
    AgentConfig,
    AgentCoreTarget,
    BridgeConfig,
    HTTPTarget,
    ReconnectConfig,
    Target,
)
from .forwarder import (
    AgentCoreForwarder,
    Forwarder,
    HTTPForwarder,
    build_forwarder,
)
from .health import HealthServer

BandBridge = BandBridge

__all__ = [
    "AgentConfig",
    "AgentCoreForwarder",
    "AgentCoreTarget",
    "AgentRunner",
    "BandBridge",
    "BridgeConfig",
    "Forwarder",
    "HTTPForwarder",
    "HTTPTarget",
    "HealthServer",
    "ReconnectConfig",
    "Target",
    "BandBridge",
    "build_forwarder",
    "main",
]
