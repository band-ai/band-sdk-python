"""Keep Linux runtime capabilities separate from portable contract values."""

from __future__ import annotations

import sys

if sys.platform == "linux":
    import fcntl


class UnsupportedHostError(RuntimeError):
    """A requested local runtime guarantee is unavailable on this host."""


def require_linux() -> None:
    if sys.platform != "linux":
        raise UnsupportedHostError(
            "The local contract runner requires Linux for private state, process locking, "
            "and isolated callbacks."
        )


def require_runtime() -> None:
    require_linux()
    if sys.version_info[:2] != (3, 12):
        raise UnsupportedHostError(
            "The local contract runner requires Python 3.12 for its qualified "
            "isolated callback runtime."
        )


def exclusive_lock(descriptor: int, *, nonblocking: bool = False) -> None:
    require_linux()
    fcntl.flock(descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
