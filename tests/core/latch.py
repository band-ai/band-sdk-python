"""A delivery owner recording terminal failure notifications."""

from __future__ import annotations


class RecordingLatch:
    def __init__(self) -> None:
        self.latched: list[frozenset[str]] = []

    def latch(self, _room: str, ids: frozenset[str]) -> None:
        self.latched.append(ids)
