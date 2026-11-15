"""Identity of the current pytest-xdist worker (or a serial run).

xdist sets ``PYTEST_XDIST_WORKER`` (``gw0``, ``gw1``, …) in each worker
process and leaves it unset on a serial run. Anything that would collide
across workers (a Hypothesis example DB, a shared uv cache directory)
must key off this id. ``master`` is xdist's name for the controller /
a process that is not a worker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import settings as hypothesis_settings
from hypothesis.database import DirectoryBasedExampleDatabase
from pydantic_settings import BaseSettings, SettingsConfigDict

SERIAL_WORKER_ID = "master"
# Four in-job workers on the existing OS × extra cells. Not a GitHub shard axis.
CI_XDIST_FLAGS = ("-n 4", "--dist worksteal")

_parlant_home: Path | None = None
_previous_parlant_home: str | None = None


class XdistWorkerSettings(BaseSettings):
    """``PYTEST_XDIST_WORKER`` as a settings field, defaulting to serial."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    pytest_xdist_worker: str = SERIAL_WORKER_ID


def xdist_worker_id() -> str:
    """The current worker id, or ``SERIAL_WORKER_ID`` when running serially."""
    return XdistWorkerSettings().pytest_xdist_worker


def worker_scoped(name: str, worker: str | None = None) -> str:
    """``name`` unique to one xdist worker (or a serial run)."""
    return f"{name}-{worker if worker is not None else xdist_worker_id()}"


def hypothesis_database_dir(worker: str | None = None) -> Path:
    """Hypothesis example-database directory for one worker."""
    return Path(".hypothesis") / worker_scoped("examples", worker)


def configure_hypothesis_worker_database() -> None:
    """Point Hypothesis at a per-worker example DB so workers do not share one."""
    hypothesis_settings.register_profile(
        "band-xdist",
        database=DirectoryBasedExampleDatabase(hypothesis_database_dir()),
    )
    hypothesis_settings.load_profile("band-xdist")


def configure_parlant_worker_home() -> None:
    """Give each pytest process an isolated Parlant store for local servers.

    Parlant's file logger holds ``parlant.log`` open until interpreter shutdown,
    so pytest teardown cannot safely remove its home on Windows.
    """
    global _parlant_home, _previous_parlant_home

    if _parlant_home is not None:
        return

    worker = xdist_worker_id()
    _parlant_home = Path(
        tempfile.mkdtemp(prefix=worker_scoped("band-parlant-home", worker) + "-")
    )
    _previous_parlant_home = os.environ.get("PARLANT_HOME")
    os.environ["PARLANT_HOME"] = str(_parlant_home)


def restore_parlant_worker_home() -> None:
    """Restore the caller's Parlant setting after pytest completes."""
    global _parlant_home, _previous_parlant_home

    if _parlant_home is None:
        return

    if os.environ.get("PARLANT_HOME") == str(_parlant_home):
        if _previous_parlant_home is None:
            os.environ.pop("PARLANT_HOME", None)
        else:
            os.environ["PARLANT_HOME"] = _previous_parlant_home
    _parlant_home = None
    _previous_parlant_home = None
