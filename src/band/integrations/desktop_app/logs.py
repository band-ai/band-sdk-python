"""First-class logging for the Desktop room view process.

A headless stdio server can only speak through logs, and Claude Desktop is not
a reliable listener: it wires some instances' stderr into its per-server log
file and others' into ``/dev/null`` (observed live). Diagnostics therefore go
to both stderr and the server's own rotating file under the state directory.
stdout is the MCP transport and must never carry a log line.

Diagnostic lines are concise ``key=value`` facts — identifiers, counts, and
outcomes. Room message content, participant names, and credentials are never
logged.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.integrations.desktop_app.event_relay import STATE_DIR

LOG_FILE = STATE_DIR / "band-room-view.log"
LOG_FORMAT = "%(asctime)s %(levelname).1s %(name)s: %(message)s"

# Loggers that narrate every request or frame. Their INFO drowns ours; their
# warnings still get through.
CHATTY_LOGGERS = (
    "httpx",
    "httpcore",
    "mcp.server.lowlevel.server",
    "phoenix_channels_python_client",
)


class LogTuning(BaseSettings):
    """Per-install logging knobs, read once at process start."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    band_log_level: str = Field(
        "INFO",
        description="Level for the room view's own loggers (DEBUG shows quiet ticks).",
    )
    band_log_max_bytes: int = Field(
        1_000_000,
        ge=10_000,
        description="Size at which the room view's own log file rotates.",
    )
    band_log_backups: int = Field(
        1,
        ge=0,
        description="Rotated log files to keep.",
    )

    @field_validator("band_log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        level = value.upper()
        if level not in logging.getLevelNamesMapping():
            raise ValueError(f"Unknown log level: {value}")
        return level


def configure() -> None:
    """Route the process's diagnostics to stderr and the rotating file."""
    tuning = LogTuning()
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    logging.basicConfig(
        level=tuning.band_log_level,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stderr),
            RotatingFileHandler(
                LOG_FILE,
                maxBytes=tuning.band_log_max_bytes,
                backupCount=tuning.band_log_backups,
            ),
        ],
    )
    for name in CHATTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
