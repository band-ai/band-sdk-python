"""Core protocols and types for composition-based architecture."""

from band.core.protocols import (
    AgentToolsProtocol,
    FrameworkAdapter,
    HistoryConverter,
    Preprocessor,
)
from band.core.simple_adapter import BatchInputAdapter, SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage

__all__ = [
    "AgentInput",
    "AgentToolsProtocol",
    "BatchInputAdapter",
    "FrameworkAdapter",
    "HistoryConverter",
    "HistoryProvider",
    "PlatformMessage",
    "Preprocessor",
    "SimpleAdapter",
]
