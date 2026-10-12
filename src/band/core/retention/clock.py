"""The time source idle deadlines are measured and fired against."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol


class Cancellable(Protocol):
    def cancel(self) -> None: ...


class RetentionClock(Protocol):
    def now(self) -> float: ...

    def call_at(self, when: float, callback: Callable[[], None]) -> Cancellable: ...


class MonotonicClock:
    """The running event loop's clock, so ``now`` and ``call_at`` agree."""

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    def call_at(self, when: float, callback: Callable[[], None]) -> Cancellable:
        return asyncio.get_running_loop().call_at(when, callback)
