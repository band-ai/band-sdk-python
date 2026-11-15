"""Signal retained process identities without reviving expired PID records."""

from __future__ import annotations

import psutil


def process_is_alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def kill_retained_process(process: psutil.Process) -> None:
    """Signal only a still-live retained process identity."""
    # A confirmed-dead psutil record no longer rechecks PID identity.
    if process_is_alive(process):
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
