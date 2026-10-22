from __future__ import annotations

import threading
from typing import Any, Protocol


class LockOwnership(Protocol):
    def _is_owned(self) -> bool: ...


class HandoffLock:
    """Make the waiting control's first inter-page turn deterministic."""

    def __init__(self, control_done: Any) -> None:
        self.lock = threading.RLock()
        self.control_done = control_done
        self.exits = 0

    def __enter__(self) -> Any:
        self.lock.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.lock.release()
        self.exits += 1
        if self.exits == 1 and not self.control_done.wait(2):
            raise AssertionError("Waiting control did not acquire a released page lock")
