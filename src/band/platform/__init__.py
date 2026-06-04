"""
Band Platform Layer - Wire-level connection to Band platform.

Components:
    BandLink: WebSocket connection + event dispatch (REST via .rest)
    PlatformEvent: Single event type for all platform events
"""

from .event import PlatformEvent
from .link import BandLink

__all__ = [
    "BandLink",
    "PlatformEvent",
]
