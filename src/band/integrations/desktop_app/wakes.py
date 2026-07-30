"""The ledger that decides, exactly once, which room mentions wake Claude."""

from __future__ import annotations

import logging
from collections import Counter

from band.integrations.desktop_app.room import RoomMessage

logger = logging.getLogger(__name__)

# How often one mention may come back for another wake. A refusal the host
# decided is dropped by the view, so a re-offer means the ask never arrived —
# and after a few of those it never will, while re-offering costs a failed
# round trip on every tick for the rest of the conversation.
MAX_REOFFERS = 3


class WakeLedger:
    """Bookkeeping for the view's wake accelerator.

    This ledger is the only place that decides to wake Claude, so a payload
    the view sees twice cannot start two turns, and a wake the host refused
    is re-offered even after the room moves on.
    """

    def __init__(self) -> None:
        self._woken: dict[str, set[str]] = {}
        self._refused: dict[str, dict[str, RoomMessage]] = {}
        self._reoffers: dict[str, Counter[str]] = {}

    def suppress(self, chat_id: str, pending: list[RoomMessage]) -> None:
        """Record mentions the Claude turn calling this tool already handles."""
        self._woken.setdefault(chat_id, set()).update(
            message.id for message in pending if message.id
        )

    def claim(self, chat_id: str, pending: list[RoomMessage]) -> list[RoomMessage]:
        """The mentions Claude still owes a turn for, each handed out once."""
        woken = self._woken.setdefault(chat_id, set())
        refused = self._refused.pop(chat_id, {})
        claimed = list(refused.values()) + [
            message
            for message in pending
            if message.id and message.id not in woken and message.id not in refused
        ]
        woken.update(message.id for message in claimed)
        return claimed

    def release(
        self,
        chat_id: str,
        message_ids: list[str],
        known: dict[str, RoomMessage],
    ) -> None:
        """Re-offer mentions whose wake never reached the host.

        Only a message this ledger actually woke, that the caller can still
        name (``known``), and that has attempts left, goes back on offer.
        """
        woken = self._woken.setdefault(chat_id, set())
        attempts = self._reoffers.setdefault(chat_id, Counter())
        reoffered, exhausted = [], []
        for identifier in message_ids:
            if identifier not in woken or identifier not in known:
                continue
            attempts[identifier] += 1
            if attempts[identifier] > MAX_REOFFERS:
                exhausted.append(identifier)
                continue
            woken.discard(identifier)
            self._refused.setdefault(chat_id, {})[identifier] = known[identifier]
            reoffered.append(identifier)
        if reoffered:
            logger.info("wake re-offered chat=%s ids=%s", chat_id, reoffered)
        if exhausted:
            logger.warning(
                "wake given up on chat=%s ids=%s after %d attempts; the monitor "
                "loop is now the only way these are answered",
                chat_id,
                exhausted,
                MAX_REOFFERS,
            )
