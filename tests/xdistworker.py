"""Identity of the current pytest-xdist worker (or a serial run).

xdist sets ``PYTEST_XDIST_WORKER`` (``gw0``, ``gw1``, …) in each worker
process and leaves it unset on a serial run. Anything that would collide
across workers (a Hypothesis example DB, a shared uv cache directory)
must key off this id. ``master`` is xdist's name for the controller /
a process that is not a worker.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import settings as hypothesis_settings
from hypothesis.database import DirectoryBasedExampleDatabase
from pydantic_settings import BaseSettings, SettingsConfigDict

SERIAL_WORKER_ID = "master"


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
