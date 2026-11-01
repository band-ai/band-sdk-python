"""Immutable originating tools views over one room-owned outgoing-send cache."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from band.runtime.activity import ActivityObserver, activity_observer_of

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol

logger = logging.getLogger(__name__)

# 30 s comfortably covers the longest observed Claude-CLI tool-call retry
# window seen in reconnect storms while staying short
# enough that a deliberate retransmission of the same message after
# minutes (e.g. a user re-asking for a status report) is not suppressed.
DEFAULT_DEDUP_TTL_SECONDS: float = 30.0

# Bound the cache so an adversarial or runaway agent cannot grow it
# without limit. Each entry is one observed (content, mentions) tuple
# per room; 64 is well beyond any realistic single-turn fan-out.
DEFAULT_DEDUP_MAX_ENTRIES: int = 64


def _normalize_mentions(
    mentions: list[str] | list[dict[str, str]] | None,
) -> frozenset[str]:
    """Reduce a mentions argument to a hashable, order-insensitive key.

    ``AgentTools.send_message`` accepts handles as plain strings (current
    contract) and as ``{"id": ..., "handle": ...}`` dicts (deprecated).
    Both shapes are reduced to the same key so a transport retry that
    happens to upgrade the encoding still dedupes.
    """
    if not mentions:
        return frozenset()
    keys: list[str] = []
    for item in mentions:
        if isinstance(item, str):
            keys.append(item)
        elif isinstance(item, dict):
            keys.append(item.get("handle") or item.get("id") or "")
    return frozenset(k for k in keys if k)


class SendDeduplication:
    """Cache policy and actual in-flight sends, shared without rebinding origins."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        label: str | None,
        clock: Callable[[], float],
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._label = label
        self._clock = clock
        self._recent: OrderedDict[tuple[str, frozenset[str]], tuple[float, Any]] = (
            OrderedDict()
        )
        self._in_flight: dict[
            tuple[str, frozenset[str]], tuple[float, asyncio.Task[Any]]
        ] = {}

    async def send(
        self,
        tools: AgentToolsProtocol,
        content: str,
        mentions: list[str] | list[dict[str, str]] | None,
    ) -> Any:
        now = self._clock()
        self._evict_expired(now)
        key = (content, _normalize_mentions(mentions))
        if (cached := self._recent.get(key)) is not None:
            logger.warning(
                "Claude send dedup suppressed duplicate room=%s content_len=%s mentions=%s",
                self._label,
                len(content),
                len(key[1]),
            )
            return cached[1]
        if (existing := self._in_flight.get(key)) is not None:
            task = existing[1]
        else:
            task = asyncio.create_task(tools.send_message(content, mentions))
            self._in_flight[key] = (now, task)
            task.add_done_callback(partial(self._finish, key, now))
        return await asyncio.shield(task)

    def _finish(
        self, key: tuple[str, frozenset[str]], timestamp: float, task: asyncio.Task[Any]
    ) -> None:
        try:
            result = task.result()
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- a failed send must never enter the successful cache
            result = None
            successful = False
        else:
            successful = True
        if self._in_flight.get(key, (None, None))[1] is not task:
            return
        del self._in_flight[key]
        if successful:
            self._recent[key] = (timestamp, result)
            while len(self._recent) > self._max_entries:
                self._recent.popitem(last=False)

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        for key, (timestamp, _) in tuple(self._in_flight.items()):
            if timestamp < cutoff:
                del self._in_flight[key]
        for key, (timestamp, _) in tuple(self._recent.items()):
            if timestamp < cutoff:
                del self._recent[key]


class DedupingAgentTools:
    """A captured origin; creating another view preserves only shared dedup state."""

    def __init__(
        self,
        inner: AgentToolsProtocol,
        *,
        ttl_seconds: float = DEFAULT_DEDUP_TTL_SECONDS,
        max_entries: int = DEFAULT_DEDUP_MAX_ENTRIES,
        label: str | None = None,
        state: SendDeduplication | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._dedup = state or SendDeduplication(
            ttl_seconds=ttl_seconds, max_entries=max_entries, label=label, clock=clock
        )

    def for_origin(self, tools: AgentToolsProtocol) -> DedupingAgentTools:
        return DedupingAgentTools(tools, state=self._dedup)

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> Any:
        return await self._dedup.send(self._inner, content, mentions)

    @property
    def _activity_observer(self) -> ActivityObserver | None:
        return activity_observer_of(self._inner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
