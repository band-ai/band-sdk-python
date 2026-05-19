"""Shared logging configuration for 20 Questions Arena."""

from __future__ import annotations

import logging
import os
from pathlib import Path

_ARENA_DIR = Path(__file__).resolve().parent
_LOG_DIR = _ARENA_DIR / "logs"


def setup_logging(level=logging.INFO, agent_tag: str | None = None):
    """Configure logging to console + rotating file.

    Logs are written to ``examples/20-questions-arena/logs/<agent_tag>.log`` (or
    ``20-questions-arena.log`` when *agent_tag* is not provided).  Console output
    stays the same as before — only ``thenvoi.*`` loggers at *level*,
    everything else at WARNING.

    Args:
        level: Log level for thenvoi loggers (default INFO).
        agent_tag: Short label used for the log filename
            (e.g. ``"guesser_nano"``, ``"thinker"``).
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Console handler (WARNING for noisy deps, *level* for thenvoi)
    logging.basicConfig(level=logging.WARNING, format=fmt, datefmt=datefmt)
    logging.getLogger("thenvoi").setLevel(level)

    # File handler — captures everything at DEBUG for post-mortem analysis
    _LOG_DIR.mkdir(exist_ok=True)
    filename = f"{agent_tag}.log" if agent_tag else "20-questions-arena.log"
    from logging.handlers import RotatingFileHandler

    fh = RotatingFileHandler(
        os.fspath(_LOG_DIR / filename),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logging.getLogger().addHandler(fh)

    # Also capture phoenix-channels and langchain at DEBUG in the file
    for name in (
        "phoenix_channels_python_client",
        "langchain",
        "langchain_openai",
        "langchain_anthropic",
    ):
        logging.getLogger(name).setLevel(logging.DEBUG)
