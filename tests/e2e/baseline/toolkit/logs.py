"""Scoped SDK log levels for baseline scenarios."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from band.logging_config import more_verbose


@contextmanager
def sdk_logs_at(package: ModuleType, level: int) -> Iterator[None]:
    """Let ``package``'s records at ``level`` reach pytest's captured log, which
    a failing test prints; the root logger's WARNING default otherwise drops them.
    Never raises the threshold, so a run at ``--log-level=DEBUG`` keeps its DEBUG."""
    sdk_logger = logging.getLogger(package.__name__)
    previous_level = sdk_logger.level
    sdk_logger.setLevel(more_verbose(level, sdk_logger.getEffectiveLevel()))
    try:
        yield
    finally:
        sdk_logger.setLevel(previous_level)
