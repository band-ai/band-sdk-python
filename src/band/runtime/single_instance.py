"""Single-instance guard: one running process per agent id per host.

Two processes running the same agent id silently corrupt each other:
both claim in-flight room messages (the startup recovery sweep has no
liveness check), and stateful adapters resume the same on-disk sessions,
splitting one conversation across two processes. The runtime therefore
holds an OS-level lock per agent id for as long as the agent runs.

The lock is advisory and held on an open file descriptor, so the OS
releases it the moment the process exits — crashes included. Lock files
are never unlinked (removing a lock file races against a concurrent
acquire on the recreated path); a leftover file without a holder carries
no lock and is harmless.

Scope, honestly stated: the lock file lives in the process's temp dir,
so the guard only catches duplicates that share it. Processes with
divergent ``TMPDIR`` (e.g. systemd ``PrivateTmp``), separate containers,
or different hosts do not contend — deployments that shard one agent id
across such boundaries need platform-level dedup, not this guard. It
also guards only the long-lived Agent runtime; one-shot invocations
(``band.runtime.oneshot``) rely on server-arbitrated message claiming
instead of host locks.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from band.core.exceptions import BandConfigError

try:
    import fcntl
except ImportError:  # non-POSIX; the guard degrades to a warning
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Live holders in this process, by agent id. Exists for lifecycles that
# skip normal unwinding (e.g. a test runner's signal kill bypasses
# ``Agent.stop``): the leaked fd would otherwise pin the lock for the
# process lifetime. ``release_all_held`` lets such harnesses reap.
_held: dict[str, SingleInstanceGuard] = {}


def release_all_held() -> list[str]:
    """Release every lock still held by this process; return the agent ids.

    A cleanup tool for harnesses whose failure paths can kill an agent
    without unwinding it (pytest-timeout's signal method). Normal code
    paths release via ``PlatformRuntime.stop`` and never need this.
    """
    released = list(_held)
    for guard in list(_held.values()):
        guard.release()
    return released


class SingleInstanceGuard:
    """Holds the host-wide run lock for one agent id.

    ``acquire()`` raises :class:`BandConfigError` when another process
    (or another guard in this process) already holds the agent's lock.
    ``release()`` is idempotent and safe to call from ``finally`` blocks.
    """

    def __init__(self, agent_id: str, *, lock_dir: str | Path | None = None) -> None:
        directory = Path(lock_dir) if lock_dir else Path(tempfile.gettempdir())
        self.lock_path = directory / f"band-agent-{agent_id}.lock"
        self._agent_id = agent_id
        self._fd: int | None = None

    def acquire(self) -> None:
        """Take the agent's run lock, failing fast when it is held."""
        if self._fd is not None:
            return
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            # e.g. EACCES on another user's lock file in a shared temp dir.
            raise BandConfigError(
                f"Cannot open the single-instance lock file {self.lock_path} "
                f"for agent {self._agent_id}: {exc}. Fix the file's "
                "permissions, or set AgentConfig(single_instance=False) to "
                "bypass the guard."
            ) from exc
        try:
            if fcntl is None:  # non-POSIX platform; degrade openly
                logger.warning("No file-locking primitive; single-instance guard inert")
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:  # the one errno that means contention
            os.close(fd)
            raise BandConfigError(
                f"Agent {self._agent_id} is already running on this host "
                f"(lock: {self.lock_path}). Two instances of one agent steal "
                "each other's room messages and split conversations — stop "
                "the other process first, or set "
                "AgentConfig(single_instance=False) to bypass the guard."
            ) from exc
        except OSError as exc:  # flock unsupported (e.g. some NFS mounts)
            os.close(fd)
            raise BandConfigError(
                f"Cannot take the single-instance lock at {self.lock_path} "
                f"for agent {self._agent_id}: {exc}. Locking may be "
                "unsupported on this filesystem — set "
                "AgentConfig(single_instance=False) to bypass the guard."
            ) from exc
        self._fd = fd
        _held[self._agent_id] = self

    def release(self) -> None:
        """Drop the lock; the file stays behind (holderless, harmless)."""
        if self._fd is None:
            return
        if _held.get(self._agent_id) is self:
            del _held[self._agent_id]
        fd, self._fd = self._fd, None
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
