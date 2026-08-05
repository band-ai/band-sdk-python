"""Shared logging configuration for 20 Questions Arena."""

from __future__ import annotations

from pathlib import Path

from band import LogLevel, LogSettings

_ARENA_DIR = Path(__file__).resolve().parent
_LOG_DIR = _ARENA_DIR / "logs"

# The loggers this example wants at DEBUG. Spelled out rather than pulled from
# chatty_logger_levels(), whose membership is Band's general "quiet these down"
# set (it also covers httpx/httpcore) — not this call site's own intent.
_ARENA_FRAMEWORK_LOGGERS = (
    "phoenix_channels_python_client",
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
        extra_loggers=dict.fromkeys(_ARENA_FRAMEWORK_LOGGERS, "DEBUG"),
    )
