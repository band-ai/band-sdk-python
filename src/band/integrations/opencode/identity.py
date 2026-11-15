"""OpenCode's native ascending message identifier encoding."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from time import time_ns

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
TIMESTAMP_SHIFT = 0x1000
TIME_MASK = (1 << 48) - 1
RANDOM_LENGTH = 14


def milliseconds() -> int:
    return time_ns() // 1_000_000


class MessageIds:
    """Match native ordering within one process; clock rollback resets its counter.

    The native format supplies no global ordering across processes or beyond
    its counter's per-millisecond capacity.
    """

    def __init__(self, clock: Callable[[], int] = milliseconds) -> None:
        self._clock = clock
        self._timestamp: int | None = None
        self._counter = 0

    def next(self) -> str:
        timestamp = self._clock()
        if timestamp != self._timestamp:
            self._timestamp, self._counter = timestamp, 0
        self._counter += 1
        encoded = (timestamp * TIMESTAMP_SHIFT + self._counter) & TIME_MASK
        suffix = "".join(
            BASE62[byte % len(BASE62)] for byte in secrets.token_bytes(RANDOM_LENGTH)
        )
        return f"msg_{encoded:012x}{suffix}"
