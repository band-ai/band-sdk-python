"""Chat-mediated decisions over ``band_sdk_core.DecisionRegistry``: core owns
the claim, eviction, and ticket rules; this wrapper owns payloads, timers, and
reads, from an ordered map that mirrors core's order by construction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Literal, TypeVar, overload

import band_sdk_core
from band_sdk_core import CancelledDecisions, ClaimOutcome

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class Timeout(Enum):
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, eq=False)
class DecisionEntry(Generic[T]):
    """One registration: the handle its asker, timer, and waiter act through.
    Its ticket goes stale once a keyed redelivery replaces it."""

    token: str
    ticket: int
    payload: T


class DecisionRegistry(Mapping[str, T]):
    """Pending asks by token, oldest first. ``max_pending=None`` never evicts."""

    def __init__(self, *, max_pending: int | None = None) -> None:
        self._core = band_sdk_core.DecisionRegistry(max_pending=max_pending)
        self._entries: dict[str, DecisionEntry[T]] = {}
        self._timeouts: dict[str, asyncio.Task[None]] = {}

    def __getitem__(self, token: str) -> T:
        return self._entries[token].payload

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @overload
    def register(
        self, payload: T, *, room_id: str | None = None
    ) -> DecisionEntry[T]: ...

    @overload
    def register(self, payload: T, *, key: str) -> DecisionEntry[T] | None: ...

    def register(
        self, payload: T, *, key: str | None = None, room_id: str | None = None
    ) -> DecisionEntry[T] | None:
        """Add ``payload`` under ``key``, or under a minted token scoped to
        ``room_id``. A redelivered ``key`` replaces its unclaimed predecessor
        in place; ``None`` if it's claimed."""
        if key is None:
            registered = self._core.register_minted(room_id)
        elif room_id is None:
            registered = self._core.register_keyed(key)
        else:
            raise TypeError("register takes key or room_id, not both")
        match registered:
            case None if key is None:
                raise RuntimeError("DecisionRegistry ran out of tickets")
            case None:
                return None
            case (token, ticket):
                self._cancel_timeout(token)
                entry = DecisionEntry(token=token, ticket=ticket, payload=payload)
                self._entries[token] = entry
                return entry

    def start_timeout(
        self,
        entry: DecisionEntry[T],
        seconds: float,
        on_timeout: Callable[[DecisionEntry[T]], Awaitable[None]],
    ) -> None:
        """Claim ``entry`` after ``seconds`` and hand it to ``on_timeout``,
        unless a reply claims it first."""
        if not self._holds(entry):
            return

        async def expire() -> None:
            await asyncio.sleep(seconds)
            if self._claim(entry) is ClaimOutcome.Claimed:
                await on_timeout(entry)

        self._cancel_timeout(entry.token)
        self._timeouts[entry.token] = asyncio.create_task(expire())

    def try_claim(self, token: str) -> DecisionEntry[T] | None:
        """Take ownership of whatever registration holds ``token``, or ``None``
        if someone else has.

        A claimant must resolve the ask without awaiting in between: an asker
        blocked in :meth:`wait` defers to the claim with no deadline of its own.
        """
        if (entry := self._entries.get(token)) is None:
            return None
        return entry if self._claim(entry) is ClaimOutcome.Claimed else None

    def withdraw(self, entry: DecisionEntry[T]) -> bool:
        """Drop ``entry`` if nobody has claimed it; ``False`` when a claimant
        owns it or it was already replaced or removed."""
        if not (self._holds(entry) and self._core.withdraw(entry.token, entry.ticket)):
            return False
        self._discard(entry.token)
        return True

    def forget(self, entry: DecisionEntry[T]) -> None:
        """Drop ``entry``, claimed or not, leaving any newer registration of
        its token intact."""
        if self._holds(entry) and self._core.forget(entry.token, entry.ticket):
            self._discard(entry.token)

    def unclaimed(self) -> list[DecisionEntry[T]]:
        """Entries still awaiting an answer -- what a room should see as pending."""
        return self._entries_for(self._core.unclaimed())

    def unclaimed_in_room(self, room_id: str) -> list[DecisionEntry[T]]:
        return self._entries_for(self._core.unclaimed_in_room(room_id))

    def unclaimed_count(self) -> int:
        return self._core.unclaimed_count()

    def oldest_unclaimed(self) -> DecisionEntry[T] | None:
        token = self._core.oldest_unclaimed()
        return None if token is None else self._entries[token]

    def has_claimed(self) -> bool:
        """Whether a claimant is still resolving some entry."""
        return len(self) > self.unclaimed_count()

    def evict_oldest(self) -> DecisionEntry[T] | None:
        """At capacity, remove the oldest unclaimed entry for the caller to resolve."""
        token = self._core.evict_oldest()
        return None if token is None else self._discard(token)

    def cancel_all(self) -> list[DecisionEntry[T]]:
        """Remove every entry, returning the unclaimed ones for the caller to
        resolve -- a claimed entry's claimant still resolves it."""
        return self._drop_cancelled(self._core.cancel_all())

    def cancel_room(self, room_id: str) -> list[DecisionEntry[T]]:
        """:meth:`cancel_all`, limited to entries registered in ``room_id``."""
        return self._drop_cancelled(self._core.cancel_room(room_id))

    async def wait(
        self, entry: DecisionEntry[T], future: asyncio.Future[R], *, timeout_s: float
    ) -> R | Literal[Timeout.TIMED_OUT]:
        """The answer to ``entry``, or ``TIMED_OUT`` if the deadline claims it
        first or it was replaced; a reply that claimed it first is always
        waited for."""
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout_s)
        except TimeoutError:
            # Whoever removed the ask in the deadline's own tick resolved it.
            if future.done():
                return future.result()
            if self._claim(entry) is ClaimOutcome.AlreadyClaimed:
                logger.debug(
                    "Decision %s: claimed before the deadline, awaiting it",
                    entry.token,
                )
                return await future
            return Timeout.TIMED_OUT
        finally:
            self.forget(entry)

    def _holds(self, entry: DecisionEntry[T]) -> bool:
        # Tickets are per registry, so an entry from a registry a room has
        # since replaced could otherwise match a fresh one's token and ticket.
        return self._entries.get(entry.token) is entry

    def _claim(self, entry: DecisionEntry[T]) -> ClaimOutcome:
        if not self._holds(entry):
            return ClaimOutcome.Stale
        outcome = self._core.try_claim(entry.token, entry.ticket)
        if outcome is ClaimOutcome.Claimed:
            self._cancel_timeout(entry.token)
        return outcome

    def _entries_for(self, tokens: list[str]) -> list[DecisionEntry[T]]:
        return [self._entries[token] for token in tokens]

    def _drop_cancelled(self, cancelled: CancelledDecisions) -> list[DecisionEntry[T]]:
        for token in cancelled.claimed:
            self._discard(token)
        return [self._discard(token) for token in cancelled.unclaimed]

    def _discard(self, token: str) -> DecisionEntry[T]:
        self._cancel_timeout(token)
        return self._entries.pop(token)

    def _cancel_timeout(self, token: str) -> None:
        # An expiry claims from inside its own timeout task and then owns the
        # outcome, so that task is detached rather than cancelled: nothing may
        # interrupt its on_timeout callback, including a later cancel_all.
        task = self._timeouts.pop(token, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
