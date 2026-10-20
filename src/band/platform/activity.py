"""Process-wide negotiation for optional room activity detail.

The first endpoint defines support for the process. A detail rejection on any
endpoint permanently downgrades every link; boolean liveness remains available.
"""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import Future
from threading import Lock

import httpx

logger = logging.getLogger(__name__)
MINIMUM_ACTIVITY_VERSION = (1, 25, 0)
RELEASE_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")


class ActivitySupport:
    """Share discovery without retaining primitives owned by an asyncio loop."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._discovery: Future[bool] | None = None
        self._disabled = False

    @property
    def supported(self) -> bool:
        with self._lock:
            return (
                not self._disabled
                and self._discovery is not None
                and self._discovery.done()
                and self._discovery.result()
            )

    def disable(self) -> None:
        with self._lock:
            already_disabled = self._disabled
            self._disabled = True
        if not already_disabled:
            logger.info(
                "Activity detail rejected; using boolean reporting for this process"
            )

    async def allows(self, rest_url: str, budget: float) -> bool:
        with self._lock:
            if self._disabled:
                return False
            discovery = self._discovery
            owner = discovery is None
            if discovery is None:
                discovery = self._discovery = Future()
        if owner:
            supported = False
            try:
                async with asyncio.timeout(budget):
                    supported = await self._discover(rest_url, budget)
            except (httpx.HTTPError, ValueError, TimeoutError):
                pass
            finally:
                discovery.set_result(supported)
                if not supported:
                    logger.info(
                        "Activity detail unavailable; using boolean reporting for this process"
                    )
        else:
            try:
                async with asyncio.timeout(budget):
                    await asyncio.shield(asyncio.wrap_future(discovery))
            except TimeoutError:
                return False
        return self.supported

    @staticmethod
    async def _discover(rest_url: str, budget: float) -> bool:
        async with httpx.AsyncClient(timeout=budget) as client:
            response = await client.get(f"{rest_url.rstrip('/')}/api/version")
            response.raise_for_status()
            data = response.json()
        version = data.get("agent_version") if isinstance(data, dict) else None
        match = RELEASE_VERSION.fullmatch(version) if isinstance(version, str) else None
        return (
            match is not None
            and tuple(map(int, match.groups())) >= MINIMUM_ACTIVITY_VERSION
        )


activity_support = ActivitySupport()
