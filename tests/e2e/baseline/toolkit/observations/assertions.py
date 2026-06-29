"""Shared tolerant assertions for message-list observation collections.

``Replies`` and ``Events`` are both lists of messages exposing a string
``content``, and both want the same two tolerant checks -- a floor on count and a
case-insensitive any-of substring over content. Those live here as a mixin so the
collections stay DRY; failure messages name the message kind from the
collection's ``MESSAGE_TYPE`` (no separate label constant), and each adds its own
type-specific assertions on top.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from band.core.types import MessageType

from tests.e2e.baseline.toolkit.observations.matching import tolerant_match


class ContentAssertions:
    """Mixin: tolerant count/content assertions for a ``list`` of messages with a
    string ``content`` attribute. Mixed in *before* ``list`` so ``len(self)`` and
    iteration resolve to the list.

    Failure messages name the message kind from ``MESSAGE_TYPE`` (the same
    ``MessageType`` the collection already carries), so there is no separate
    label constant to drift -- ``Events`` subclasses set their type, ``Replies``
    sets ``MessageType.TEXT``.
    """

    MESSAGE_TYPE: ClassVar[MessageType | None] = None

    def _noun(self) -> str:
        return f"{self.MESSAGE_TYPE.value} message" if self.MESSAGE_TYPE else "message"

    def assert_at_least(self, n: int) -> None:
        """Assert a threshold-of-N items (a floor, never an exact count)."""
        count = len(self)  # type: ignore[arg-type]
        if count < n:
            raise AssertionError(
                f"expected at least {n} {self._noun()}(s), got {count}"
            )

    def assert_contains_any(self, options: Iterable[str]) -> None:
        """Assert some item's content contains any of ``options`` (case-insensitive
        substring via :func:`tolerant_match`). Any-of, not all-of, so paraphrasing
        doesn't break it. Routing through ``tolerant_match`` means an empty option
        matches only empty content (never everything), and an empty collection
        matches nothing -- so a broken setup fails loudly instead of passing.

        Matching is per message: an option must appear within a single message's
        content, not span the boundary between two (which a real marker never does).
        """
        options = list(options)
        if not any(
            tolerant_match(option, item.content)  # type: ignore[attr-defined]
            for item in self
            for option in options
        ):
            haystack = "\n".join(item.content for item in self)  # type: ignore[attr-defined]
            raise AssertionError(
                f"no {self._noun()} contained any of {options}:\n{haystack}"
            )
