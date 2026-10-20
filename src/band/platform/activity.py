"""Process-wide negotiation for optional room activity detail.

Every agent API response names its version in ``X-API-Version``. Detail is sent
once any activity response reported a supporting version. A detail rejection on
any link permanently downgrades every link; boolean liveness remains available.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from threading import Lock

logger = logging.getLogger(__name__)
# The first agent API that accepts ``activity``; older ones reject it with 422.
MINIMUM_ACTIVITY_VERSION = (1, 25, 0)
API_VERSION_HEADER = "x-api-version"
RELEASE_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")


def _supports_detail(version: str | None) -> bool:
    match = RELEASE_VERSION.fullmatch(version) if version is not None else None
    return (
        match is not None
        and tuple(map(int, match.groups())) >= MINIMUM_ACTIVITY_VERSION
    )


class ActivitySupport:
    """Learn detail support from response headers; a rejection latches it off."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._learned = False
        self._disabled = False

    @property
    def supported(self) -> bool:
        with self._lock:
            return self._learned and not self._disabled

    def observe(self, headers: Mapping[str, str]) -> None:
        version = headers.get(API_VERSION_HEADER)
        if not _supports_detail(version):
            logger.debug(
                "Agent API version %r does not advertise activity detail", version
            )
            return
        with self._lock:
            self._learned = True

    def disable(self, *, room_id: str, field: str) -> None:
        with self._lock:
            already_disabled = self._disabled
            self._disabled = True
        if not already_disabled:
            logger.info(
                "Activity report with detail returned 422 in room %s (field %s); "
                "using boolean reporting for this process",
                room_id,
                field,
            )


activity_support = ActivitySupport()
