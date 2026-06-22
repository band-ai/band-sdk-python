"""Slack event-ID dedup cache, shared by both transports.

Slack can deliver the same Events API event more than once — the HTTP
transport retries unacked deliveries, and Socket Mode can redeliver
across reconnects. The downstream pipeline is not idempotent (it creates
rooms, mirrors turns, and invokes the brain — i.e. spends LLM tokens),
so each transport dedups on the stable ``event_id`` before dispatching.

This lives in its own module (rather than ``server.py``) so the Socket
Mode transport can reuse it without importing Starlette.
"""

from __future__ import annotations

import collections

# Default bounded cache size for Slack event-ID dedup. Slack retries
# within ~10 minutes of an unacked event; sizing for several minutes of
# events at moderate throughput gives comfortable headroom.
DEFAULT_SEEN_EVENTS_CACHE_SIZE = 10_000


class _SeenEvents:
    """LRU-bounded set of recently-seen Slack ``event_id`` values.

    Used to dedup Slack redeliveries: any event with an ``event_id`` we've
    already processed is dropped. The cache is bounded so it can't grow
    unbounded over the process lifetime; the eviction policy is
    least-recently-seen.

    Thread-safety: not used from multiple threads. The Slack handlers
    run on a single asyncio event loop, so we don't need locking.
    """

    def __init__(self, max_size: int = DEFAULT_SEEN_EVENTS_CACHE_SIZE) -> None:
        self._seen: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._max_size = max_size

    def is_dupe(self, event_id: str) -> bool:
        """Return True if ``event_id`` was seen before. Records it either way."""
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return True
        self._seen[event_id] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False

    def __len__(self) -> int:
        return len(self._seen)
