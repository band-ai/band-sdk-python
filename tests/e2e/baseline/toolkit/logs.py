"""Scoped SDK log levels for baseline scenarios."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from band.logging_config import more_verbose


@contextmanager
def sdk_logs_at(
    package: ModuleType, level: int, *, path: Path | None = None
) -> Iterator[None]:
    """Let ``package``'s records at ``level`` reach pytest's captured log, which
    a failing test prints; the root logger's WARNING default otherwise drops them.
    Never raises the threshold, so a run at ``--log-level=DEBUG`` keeps its DEBUG."""
    sdk_logger = logging.getLogger(package.__name__)
    previous_level = sdk_logger.level
    sdk_logger.setLevel(more_verbose(level, sdk_logger.getEffectiveLevel()))
    handler: logging.FileHandler | None = None
    try:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            # Diagnostic callers select packages with content-free INFO records.
            # Other levels can contain tool inputs, provider text or exceptions.
            handler.addFilter(lambda record: record.levelno == logging.INFO)
            formatter = logging.Formatter(
                "%(asctime)s.%(msecs)03dZ %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
            formatter.converter = time.gmtime
            handler.setFormatter(formatter)
            sdk_logger.addHandler(handler)
        yield
    finally:
        if handler is not None:
            sdk_logger.removeHandler(handler)
            handler.close()
        sdk_logger.setLevel(previous_level)
