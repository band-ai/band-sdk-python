"""Core protocols and types for composition-based architecture."""

from band.core.protocols import (
    AgentToolsProtocol,
    FrameworkAdapter,
    HistoryConverter,
    Preprocessor,
)
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage

__all__ = [
    "AgentInput",
    "AgentToolsProtocol",
    "FrameworkAdapter",
    "HistoryConverter",
    "HistoryProvider",
    "PlatformMessage",
    "Preprocessor",
    "SimpleAdapter",
]
