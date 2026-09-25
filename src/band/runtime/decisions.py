"""Token lifecycle for chat-mediated decisions.

A "decision" is the pattern several adapters need: post something to a room,
wait for a reply naming a token, with a timeout and a capacity bound. This
module owns only that lifecycle -- token issuance, the claim guard that lets
a timeout and a room reply race safely, and eviction. What resolving a
decision *does* stays adapter-owned: a bare `asyncio.Future` for one adapter,
a fallible network call for another.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar
from uuid import uuid4

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class DecisionEntry(Generic[T]):
    token: str
    payload: T
    claimed: bool = False
    timeout_task: asyncio.Task[None] | None = None


class DecisionRegistry(Generic[T]):
    """One room's (or one adapter's) set of outstanding chat-mediated asks.

    ``max_pending=None`` (the default) never evicts. A capacity bound is
    opt-in per instance, not a property of the mechanism itself.
    """

    def __init__(self, *, max_pending: int | None = None) -> None:
        self._entries: dict[str, DecisionEntry[T]] = {}
        self._max_pending = max_pending

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, token: str) -> T | None:
        """The payload at ``token``, claimed or not -- like ``dict.get``.

        Claimed-ness is a separate question (``entries()`` exposes it); a
        caller that must not act on an already-claimed entry uses
        ``try_claim`` instead, which is the one gate that actually enforces
        that.
        """
        entry = self._entries.get(token)
        return None if entry is None else entry.payload

    def __contains__(self, token: object) -> bool:
        return token in self._entries

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def tokens(self) -> list[str]:
        return list(self._entries)

    def values(self) -> list[T]:
        return [entry.payload for entry in self._entries.values()]

    def items(self) -> list[tuple[str, T]]:
        return [(entry.token, entry.payload) for entry in self._entries.values()]

    def entries(self) -> list[DecisionEntry[T]]:
        """Snapshot of every current entry, claimed or not.

        For the aggregate queries a caller can't get from ``get``/``tokens``
        alone -- e.g. "is anything still unclaimed".
        """
        return list(self._entries.values())

    def register(self, payload: T, *, key: str | None = None) -> str | None:
        """Insert a new entry, keyed by ``key`` or a minted token.

        A redelivery under the same ``key`` supersedes the existing entry --
        cancelling its timer -- unless that entry is already claimed, in
        which case an in-flight resolution must not be displaced: ``None``
        is returned and the existing entry is left untouched.
        """
        if key is not None:
            existing = self._entries.get(key)
            if existing is not None:
                if existing.claimed:
                    return None
                _cancel_timeout(existing)
        token = key if key is not None else uuid4().hex[:8]
        self._entries[token] = DecisionEntry(token=token, payload=payload)
        return token

    def start_timeout(
        self, token: str, seconds: float, on_timeout: Callable[[T], Awaitable[None]]
    ) -> None:
        """Schedule a cancelable timeout that claims before firing.

        A room reply that wins the race against this timer makes ``on_timeout``
        a no-op -- ``try_claim`` inside the task returns ``None`` and the task
        exits without invoking it.
        """

        async def _expire() -> None:
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                return
            payload = self.try_claim(token)
            if payload is not None:
                await on_timeout(payload)

        entry = self._entries.get(token)
        if entry is None:
            return
        entry.timeout_task = asyncio.create_task(_expire())

    def try_claim(self, token: str) -> T | None:
        """Atomically claim the entry at ``token``, or ``None`` if it is
        already claimed or was never registered. The one gate every
        resolution path -- a room reply, a timeout, an eviction, a cancel --
        must pass through, so at most one of them ever acts on a given token.
        """
        entry = self._entries.get(token)
        if entry is None or entry.claimed:
            return None
        entry.claimed = True
        _cancel_timeout(entry)
        return entry.payload

    def forget(self, token: str) -> None:
        """Drop a claimed entry once the adapter has finished acting on it."""
        self._entries.pop(token, None)

    def evict_oldest(self) -> DecisionEntry[T] | None:
        """Pop the oldest *unclaimed* entry (by registration order) once at
        capacity, else ``None``.

        Only meaningful when ``max_pending`` is set -- an unbounded registry
        never evicts. Skips an entry already claimed by an in-flight
        resolution: it isn't idle capacity to reclaim, and evicting it out
        from under that resolution would race it.
        """
        if self._max_pending is None or len(self._entries) < self._max_pending:
            return None
        for token, entry in self._entries.items():
            if not entry.claimed:
                del self._entries[token]
                _cancel_timeout(entry)
                return entry
        return None

    def cancel_all(
        self, predicate: Callable[[T], bool] | None = None
    ) -> list[DecisionEntry[T]]:
        """Pop and return every entry (optionally filtered by ``predicate``),
        cancelling their timers. The caller decides what "giving up" means
        for each returned entry -- this only stops the clock and drops the
        bookkeeping.
        """
        matched = [
            entry
            for entry in self._entries.values()
            if predicate is None or predicate(entry.payload)
        ]
        for entry in matched:
            del self._entries[entry.token]
            _cancel_timeout(entry)
        return matched


async def await_decision(
    registry: DecisionRegistry[Any],
    token: str,
    future: asyncio.Future[R],
    *,
    timeout_s: float,
    forced_value: R,
    on_timeout: Callable[[], Awaitable[None]] | None = None,
) -> R:
    """The shared shape for an asker that blocks on a ``Future``.

    Waits up to ``timeout_s`` for ``future``, forcing ``forced_value`` onto
    it through the same ``try_claim`` gate a room reply must win against.
    ``on_timeout`` fires only when this call itself wins that race -- not
    when a reply claimed the entry first, in which case the reply's own
    result is returned instead, exactly as if this call had never timed out.
    Always forgets ``token`` on the way out.

    Registering the payload, evicting the oldest entry if at capacity, and
    whatever must happen between registering and waiting (posting the room
    prompt, typically) are the caller's job, before calling this -- this
    function only owns the wait/timeout/claim step, not the whole lifecycle.

    ``asyncio.wait_for`` cancels ``future`` the instant it raises
    ``TimeoutError``, so this never tries to read a result back off it --
    only whether ``try_claim`` still won is used, to decide whether to
    notify/log (a reply that claimed it first already has its own outcome
    to report, and doesn't need a second, contradictory one from here).
    """
    try:
        return await asyncio.wait_for(future, timeout=timeout_s)
    except TimeoutError:
        if registry.try_claim(token) is not None:
            if not future.done():
                future.set_result(forced_value)
            if on_timeout is not None:
                await on_timeout()
        return forced_value
    finally:
        registry.forget(token)


def _cancel_timeout(entry: DecisionEntry[Any]) -> None:
    if entry.timeout_task and entry.timeout_task is not asyncio.current_task():
        entry.timeout_task.cancel()
        entry.timeout_task = None
