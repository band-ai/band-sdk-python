"""Shared logging configuration for 20 Questions Arena."""

from __future__ import annotations

from pathlib import Path

from band import LogLevel, LogSettings, chatty_logger_levels

_ARENA_DIR = Path(__file__).resolve().parent
_LOG_DIR = _ARENA_DIR / "logs"

_ARENA_FRAMEWORK_LOGGERS = (
    "langchain",
    "langchain_openai",
    "langchain_anthropic",
)


def setup_logging(
    level: LogLevel | None = None,
    agent_tag: str | None = None,
) -> None:
    """Configure logging to console + rotating DEBUG file.

    Logs are written to ``examples/20-questions-arena/logs/<agent_tag>.log`` (or
    ``20-questions-arena.log`` when *agent_tag* is not provided). Console stays
    at the configured application level while the file captures DEBUG detail.
    """
    filename = f"{agent_tag}.log" if agent_tag else "20-questions-arena.log"
    settings = LogSettings.create(
        log_level=level,
        log_file=_LOG_DIR / filename,
        log_file_level="DEBUG",
        log_max_bytes=5 * 1024 * 1024,
        log_backups=3,
    )
    settings.configure(
        extra_loggers={
            **chatty_logger_levels("DEBUG"),
            **dict.fromkeys(_ARENA_FRAMEWORK_LOGGERS, "DEBUG"),
        },
    )
