"""Chat-mediated decisions: whoever claims a pending ask owns its outcome."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Generic, Literal, TypeVar, overload
from uuid import uuid4

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    NOT_PENDING = "not_pending"
    UNAUTHORIZED = "unauthorized"


class Timeout(Enum):
    TIMED_OUT = "timed_out"


@dataclass
class DecisionEntry(Generic[T]):
    token: str
    payload: T
    claimed: bool = False
    timeout_task: asyncio.Task[None] | None = None


class DecisionRegistry(Mapping[str, T]):
    """Pending asks by token. ``max_pending=None`` never evicts;
    ``authorized_senders=None`` lets anyone reply."""

    def __init__(
        self,
        *,
        max_pending: int | None = None,
        authorized_senders: Collection[str] | None = None,
    ) -> None:
        self._entries: dict[str, DecisionEntry[T]] = {}
        self._max_pending = max_pending
        self._authorized_senders = authorized_senders

    def __getitem__(self, token: str) -> T:
        return self._entries[token].payload

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[DecisionEntry[T]]:
        return list(self._entries.values())

    def unclaimed(self) -> list[DecisionEntry[T]]:
        """Entries still awaiting an answer -- what a room should see as pending."""
        return [entry for entry in self._entries.values() if not entry.claimed]

    @overload
    def register(self, payload: T) -> str: ...

    @overload
    def register(self, payload: T, *, key: str) -> str | None: ...

    def register(self, payload: T, *, key: str | None = None) -> str | None:
        """Add ``payload`` under ``key`` or a minted token. A redelivered
        ``key`` replaces its unclaimed predecessor; ``None`` if it's claimed."""
        if key is not None and (existing := self._entries.get(key)) is not None:
            if existing.claimed:
                return None
            _cancel_timeout(existing)
        token = key if key is not None else uuid4().hex[:8]
        self._entries[token] = DecisionEntry(token=token, payload=payload)
        return token

    def start_timeout(
        self, token: str, seconds: float, on_timeout: Callable[[T], Awaitable[None]]
    ) -> None:
        if (entry := self._entries.get(token)) is None:
            return

        async def expire() -> None:
            await asyncio.sleep(seconds)
            if (payload := self.try_claim(token)) is not None:
                await on_timeout(payload)

        entry.timeout_task = asyncio.create_task(expire())

    def try_claim(self, token: str) -> T | None:
        if (entry := self._entries.get(token)) is None or entry.claimed:
            return None
        entry.claimed = True
        _cancel_timeout(entry)
        return entry.payload

    def is_authorized(self, sender_id: str | None) -> bool:
        return self._authorized_senders is None or sender_id in self._authorized_senders

    def claim_reply(self, token: str, sender_id: str | None) -> ClaimOutcome:
        if not self.is_authorized(sender_id):
            return ClaimOutcome.UNAUTHORIZED
        if self.try_claim(token) is None:
            return ClaimOutcome.NOT_PENDING
        return ClaimOutcome.CLAIMED

    def forget(self, token: str) -> None:
        self._entries.pop(token, None)

    def evict_oldest(self) -> DecisionEntry[T] | None:
        """At capacity, remove the oldest unclaimed entry for the caller to resolve."""
        if self._max_pending is None or len(self._entries) < self._max_pending:
            return None
        if not (unclaimed := self.unclaimed()):
            return None
        self._drop(oldest := unclaimed[0])
        return oldest

    def cancel_all(
        self, predicate: Callable[[T], bool] | None = None
    ) -> list[DecisionEntry[T]]:
        """Remove every matching entry, returning the unclaimed ones for the
        caller to resolve -- a claimed entry's claimant still resolves it."""
        matched = [
            entry
            for entry in self._entries.values()
            if predicate is None or predicate(entry.payload)
        ]
        for entry in matched:
            self._drop(entry)
        return [entry for entry in matched if not entry.claimed]

    async def wait(
        self, token: str, future: asyncio.Future[R], *, timeout_s: float
    ) -> R | Literal[Timeout.TIMED_OUT]:
        """The answer to ``token``, or ``TIMED_OUT`` if the timeout claims it
        first; a reply that claimed it first is always waited for."""
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout_s)
        except TimeoutError:
            if self.try_claim(token) is None:
                logger.debug(
                    "Decision %s: claimed before the deadline, awaiting it", token
                )
                return await future
            return Timeout.TIMED_OUT
        finally:
            self.forget(token)

    def _drop(self, entry: DecisionEntry[T]) -> None:
        del self._entries[entry.token]
        _cancel_timeout(entry)


def _cancel_timeout(entry: DecisionEntry[Any]) -> None:
    # An expiry claims from inside its own timeout task; cancelling that task
    # would abort the on_timeout callback at its next await.
    if entry.timeout_task and entry.timeout_task is not asyncio.current_task():
        entry.timeout_task.cancel()
        entry.timeout_task = None
