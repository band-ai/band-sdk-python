"""Sender authorization for chat-mediated decisions."""

from __future__ import annotations

from collections.abc import Collection


def is_sender_authorized(
    sender_id: str | None, allowed: Collection[str] | None
) -> bool:
    """Whether ``sender_id`` may resolve a decision gated by ``allowed``.

    ``None`` allows anyone. A non-``None`` collection -- including an empty
    one -- allows only members, so an empty collection means nobody is
    authorized rather than everybody.
    """
    return allowed is None or sender_id in allowed
