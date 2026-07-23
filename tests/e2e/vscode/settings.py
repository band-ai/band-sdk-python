"""Settings for the Copilot-in-VS-Code validation suite.

Only the suite's own knobs live here. Band endpoints, credentials, and the
judge/model configuration keep coming from ``tests.e2e.baseline.settings``
(whose module import already loads ``.env.test``) — never duplicated.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# Importing for the .env.test side effect keeps the two settings sources in
# lockstep: both read the same already-loaded environment.
import tests.e2e.baseline.settings  # noqa: F401


class VSCodeChatSettings(BaseSettings):
    """Env knobs for driving a real signed-in VS Code window (field == env var)."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    vscode_chat_tests_enabled: bool = False  # VSCODE_CHAT_TESTS_ENABLED
    code_command: str = "code"  # CODE_COMMAND (VS Code CLI binary override)
    band_mcp_command: str = "band-mcp"  # BAND_MCP_COMMAND (e.g. "uvx band-mcp")
    scorecard_json: str = ""  # VSCODE_CHAT_SCORECARD_JSON (empty = don't emit)
    turn_timeout: int = 300  # VSCODE_CHAT_TIMEOUT (seconds per live turn)
