"""
Band Platform Layer - Wire-level connection to Band platform.

Components:
    BandLink: WebSocket connection + event dispatch (REST via .rest)
    PlatformEvent: Single event type for all platform events
"""

from band.core.settlement import SettlementResult
from band.platform.event import PlatformEvent
from band.platform.link import BandLink

__all__ = [
    "BandLink",
    "PlatformEvent",
    "SettlementResult",
]
