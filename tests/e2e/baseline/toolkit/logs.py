"""Scoped SDK log levels for baseline scenarios."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from band.logging_config import more_verbose
from tests.e2e.baseline.settings import BaselineSettings


def apply_run_log_level() -> None:
    """Raise the Band and Parlant loggers to this run's diagnostic level.

    Parlant sets its own level while the server boots, so call this again after
    the server is ready. An empty ``BAND_E2E_LOG_LEVEL`` leaves both loggers
    alone.
    """
    level_name = BaselineSettings().run.log_level.strip()
    if not level_name:
        return
    level = logging.getLevelNamesMapping()[level_name.upper()]
    for name in ("band", "parlant"):
        logging.getLogger(name).setLevel(level)


@contextmanager
def sdk_logs_at(package: ModuleType | str, level: int) -> Iterator[None]:
    """Let ``package``'s records at ``level`` reach pytest's captured log, which
    a failing test prints; the root logger's WARNING default otherwise drops them.
    Never raises the threshold, so a run at ``--log-level=DEBUG`` keeps its DEBUG."""
    sdk_logger = logging.getLogger(
        package if isinstance(package, str) else package.__name__
    )
    previous_level = sdk_logger.level
    sdk_logger.setLevel(more_verbose(level, sdk_logger.getEffectiveLevel()))
    try:
        yield
    finally:
        sdk_logger.setLevel(previous_level)
