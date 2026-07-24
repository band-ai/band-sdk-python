"""Shared message-claim ledger for the inbound delivery lifecycle."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeAlias

DEFAULT_COMPLETED_CACHE_SIZE = 500
ClaimKey: TypeAlias = tuple[str, str]


class MessageClaimRegistry:
    """Ownership ledger ensuring one execution per inbound message ID.

    Lifecycle states:

    - **in flight** — claimed by exactly one execution via ``try_claim``;
      released on failure or cancellation, never evicted.
    - **ack pending** — the handler completed but the durable processed ack
      failed; redelivery retries only the ack, never the handler. Never
      evicted (losing it would replay side effects); drains through the ack
      retry budget instead. If a room never returns, this state intentionally
      remains for the registry's lifetime rather than risk replaying effects.
    - **completed** — durably processed; kept in a bounded LRU for dedup.

    ``AgentRuntime`` owns one registry and passes it to every context it
    creates, so recreated contexts retain ownership state. Claims are keyed by
    room and message ID, preventing work in one room from affecting another.
    A context constructed standalone gets a private registry, preserving the
    previous per-context behavior.

    ``try_claim`` checks and inserts without an ``await``, so event-loop
    scheduling makes claims atomic within one runtime.

    Scope, honestly stated: this coordinates executions within one runtime
    (one event loop). It cannot coordinate separate processes, containers,
    or hosts — the platform re-serves in-flight messages to fresh actors and
    ``mark_processing`` is not an exclusive claim, so deployments that shard
    one agent id across such boundaries need a platform-level claim (see
    ``band.runtime.single_instance`` for the same boundary statement).
    """

    def __init__(self, max_completed: int = DEFAULT_COMPLETED_CACHE_SIZE) -> None:
        self.max_completed = max_completed
        self._inflight: set[ClaimKey] = set()
        self._ack_pending: OrderedDict[ClaimKey, bool] = OrderedDict()
        self._ack_retries: dict[ClaimKey, int] = {}
        self._completed_by_room: dict[str, OrderedDict[str, bool]] = {}

    def _try_claim(self, room_id: str, message_id: str) -> bool:
        """Claim a message for one execution; False if another owner holds it."""
        key = (room_id, message_id)
        if key in self._inflight:
            return False
        self._inflight.add(key)
        return True

    def _release(self, room_id: str, message_id: str) -> None:
        """Release an in-flight claim (failure, cancellation, or completion)."""
        self._inflight.discard((room_id, message_id))

    @contextmanager
    def claim(self, room_id: str, message_id: str) -> Iterator[bool]:
        """Yield whether a claim was acquired and always release acquired claims.

        Exceptions propagate to the delivery path's existing error handling;
        this context only guarantees cleanup.
        """
        acquired = self._try_claim(room_id, message_id)
        try:
            yield acquired
        finally:
            if acquired:
                self._release(room_id, message_id)

    def inflight_ids(self, room_id: str) -> set[str]:
        """Currently claimed message IDs for a room (copy)."""
        return {message_id for room, message_id in self._inflight if room == room_id}

    def is_completed(self, room_id: str, message_id: str) -> bool:
        """Whether the message already completed; a hit refreshes LRU recency."""
        completed = self._completed_by_room.get(room_id)
        if completed is None or message_id not in completed:
            return False
        completed.move_to_end(message_id)
        return True

    def completed_ids(self, room_id: str) -> list[str]:
        """Completed message IDs for a room, oldest first."""
        return list(self._completed_by_room.get(room_id, ()))

    def discard_completed(self, room_id: str) -> None:
        """Discard a removed room's durably acknowledged local cache."""
        self._completed_by_room.pop(room_id, None)

    def remember_completed(self, room_id: str, message_id: str) -> None:
        """Record durable completion and clear any pending-ack state."""
        completed = self._completed_by_room.setdefault(room_id, OrderedDict())
        completed[message_id] = True
        completed.move_to_end(message_id)
        key = (room_id, message_id)
        self._ack_pending.pop(key, None)
        self._ack_retries.pop(key, None)
        if len(completed) > self.max_completed:
            completed.popitem(last=False)

    def is_ack_pending(self, room_id: str, message_id: str) -> bool:
        """Whether the message completed locally but lacks a durable ack."""
        return (room_id, message_id) in self._ack_pending

    def remember_ack_pending(self, room_id: str, message_id: str) -> None:
        """Record local completion awaiting a durable processed ack."""
        key = (room_id, message_id)
        self._ack_pending[key] = True
        self._ack_retries.setdefault(key, 0)

    def pending_ack_ids(self, room_id: str) -> list[str]:
        """Message IDs awaiting a durable processed ack, oldest first."""
        return [message_id for room, message_id in self._ack_pending if room == room_id]

    def record_ack_retry(self, room_id: str, message_id: str) -> int:
        """Count a failed ack retry; returns the total for budget checks."""
        key = (room_id, message_id)
        retries = self._ack_retries.get(key, 0) + 1
        self._ack_retries[key] = retries
        return retries
