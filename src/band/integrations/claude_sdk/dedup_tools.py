"""
Send-message dedup wrapper for Claude SDK MCP tool invocations.

When the Claude CLI subprocess saturates the asyncio event loop (its primary
failure mode under load), several upstream paths can re-emit the
same ``band_send_message`` MCP tool call for a single LLM-intended send:

* MCP transport retries after the in-process handler takes too long to ack.
* Session resume after a Phoenix WS reconnect, when the previous response was
  still being streamed.
* A new turn produced by Claude CLI after the original ``Complete`` event has
  already fired (one ``Sending query``, one ``Complete``, two messages in
  chat).

The platform happily accepts every POST, so the duplicate becomes a visible
chat message and a charged LLM call. ``ExecutionContext._processed_ids`` only
dedupes *inbound* user messages from the platform; it has no view of outbound
tool calls produced by Claude.

This wrapper sits in front of the per-room ``AgentToolsProtocol`` that the
``ClaudeSDKAdapter`` registers in ``_room_tools[room_id]``. It dedupes
``send_message`` invocations by ``(content, frozenset(mentions))`` within a
short TTL window and returns the cached result for any repeat. Every other
tool call (``send_event``, ``add_participant``, ``lookup_peers``, ...) is
forwarded unchanged via ``__getattr__`` so the wrapper does not silently
become a stale interface when ``AgentToolsProtocol`` grows new methods.

The wrapper is intentionally scoped to ``ClaudeSDKAdapter`` because that is
the only adapter whose framework runs a heavy subprocess that can
re-issue MCP calls. Other adapters drive HTTP/LLM calls directly from the
runtime task and do not exhibit this failure mode.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

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


class DedupingAgentTools:
    """Wrap an ``AgentToolsProtocol`` to dedupe identical ``send_message`` calls.

    The wrapper is transparent for every method except ``send_message`` and
    keeps no state of its own beyond the dedup cache, so the underlying
    tools object (and its ``participants`` view) remain authoritative.

    Not declared as ``AgentToolsProtocol`` subclass: Protocol classes define
    real method slots that would shadow ``__getattr__`` and break the
    pass-through. ``AgentToolsProtocol`` is structural, so any caller that
    types against the protocol still accepts this wrapper.
    """

    def __init__(
        self,
        inner: AgentToolsProtocol,
        *,
        ttl_seconds: float = DEFAULT_DEDUP_TTL_SECONDS,
        max_entries: int = DEFAULT_DEDUP_MAX_ENTRIES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._inner = inner
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        # Ordered for LRU eviction; values are (timestamp, cached_result).
        self._recent_sends: OrderedDict[
            tuple[str, frozenset[str]], tuple[float, Any]
        ] = OrderedDict()
        # Serialize concurrent send_message calls so two racing duplicates
        # cannot both miss the cache and both POST. We never await
        # arbitrary I/O while holding this lock — only the in-memory cache
        # check and the inner send_message call.
        self._lock = asyncio.Lock()

    # --- public API used by callers -----------------------------------

    async def update_inner(self, inner: AgentToolsProtocol) -> None:
        """Swap the wrapped tools object while preserving the dedup cache.

        ``AgentTools.from_context`` is called per inbound message in
        ``preprocessing/default.py`` and produces a fresh ``AgentTools``
        instance each time. If the adapter rebuilt the wrapper on every
        call the cache would be discarded after every turn and the
        dominant INT-502 failure mode (a duplicate tool call landing
        *after* the original turn's ``Complete`` event) would still slip
        through.

        Holding the lock during the swap prevents a concurrent in-flight
        ``send_message`` from observing a torn reference: the in-flight
        call started against the previous ``_inner`` and is holding the
        lock, so this method waits until that call returns before
        installing the new tools object. Subsequent calls see the new
        ``_inner`` immediately.
        """
        async with self._lock:
            self._inner = inner

    async def send_message(
        self,
        content: str,
        mentions: list[str] | list[dict[str, str]] | None = None,
    ) -> Any:
        key = (content, _normalize_mentions(mentions))
        now = time.monotonic()

        async with self._lock:
            self._evict_expired_locked(now)

            cached = self._recent_sends.get(key)
            if cached is not None:
                _, cached_result = cached
                # Intentionally do NOT move_to_end / refresh timestamp.
                # A duplicate arriving 25 s after the original is the
                # same logical send; at T+ttl it should expire and a
                # genuinely-new identical message can go through.
                logger.warning(
                    "ClaudeSDK send_message dedup: suppressing duplicate "
                    "send to room (content_len=%d, mentions=%d)",
                    len(content),
                    len(key[1]),
                )
                return cached_result

            result = await self._inner.send_message(content, mentions)

            # Insertion order equals timestamp order because we never
            # mutate cache entries after insert.
            self._recent_sends[key] = (now, result)
            while len(self._recent_sends) > self._max_entries:
                self._recent_sends.popitem(last=False)
            return result

    # --- transparent passthrough --------------------------------------

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` only runs for attributes not found via the
        # normal MRO. ``send_message`` is defined above, so it never
        # routes here. Everything else (``send_event``, ``participants``,
        # ``get_tool_schemas``, ...) is forwarded to the wrapped tools.
        return getattr(self._inner, name)

    # --- internals ----------------------------------------------------

    def _evict_expired_locked(self, now: float) -> None:
        """Drop entries older than the TTL window. Caller holds ``_lock``.

        Cache entries are append-only after insert (see ``send_message`` —
        we never refresh timestamps on hit), so insertion order equals
        timestamp order and we can stop scanning at the first fresh entry.
        """
        cutoff = now - self._ttl_seconds
        while self._recent_sends:
            key, (ts, _result) = next(iter(self._recent_sends.items()))
            if ts >= cutoff:
                return
            self._recent_sends.pop(key, None)
