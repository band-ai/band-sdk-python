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

from band.config.logs import LogSettings
from band.integrations.desktop_app.event_relay import STATE_DIR
from band.logging_config import CHATTY_LOGGERS, LogStream

LOG_FILE = STATE_DIR / "band-room-view.log"

# Transport stack plus the MCP server's own per-frame chatter.
_DESKTOP_CHATTY_LOGGERS = (*CHATTY_LOGGERS, "mcp.server.lowlevel.server")


class DesktopLogSettings(LogSettings):
    """Desktop defaults: rotating file, uniform root level, stderr only."""

    log_max_bytes: int = 1_000_000
    log_backups: int = 1


def configure() -> None:
    """Route the process's diagnostics to stderr and the rotating file."""
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings = DesktopLogSettings(log_file=LOG_FILE, log_stream=LogStream.STDERR)
    # The room view process is diagnostics-first: raise root to the configured
    # application level so our own loggers are not gated by WARNING, then demote
    # chatty dependencies. Environment overrides still win on conflict.
    settings = settings.for_application()
    settings.configure(
        extra_loggers=dict.fromkeys(_DESKTOP_CHATTY_LOGGERS, "WARNING"),
    )
