"""
Band Platform Layer - Wire-level connection to Band platform.

Components:
    BandLink: WebSocket connection + event dispatch (REST via .rest)
    PlatformEvent: Single event type for all platform events
"""

from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.platform.message_lifecycle import SettlementResult

__all__ = [
    "BandLink",
    "PlatformEvent",
    "SettlementResult",
]
