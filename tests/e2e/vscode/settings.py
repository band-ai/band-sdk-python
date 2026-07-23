"""Settings for the Copilot-in-VS-Code validation suite.

Only the suite's own knobs live here. Band endpoints, credentials, and the
judge/model configuration keep coming from ``tests.e2e.baseline.settings``
(whose module import already loads ``.env.test``) — never duplicated.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Importing for the .env.test side effect keeps the two settings sources in
# lockstep: both read the same already-loaded environment.
import tests.e2e.baseline.settings  # noqa: F401

# Defaults chosen for unattended reruns: VS Code remembers MCP-server trust per
# configuration, so a stable workspace path + stable port keep mcp.json
# byte-identical across runs — one trust approval ever, then no prompts.
# Deliberately NOT under a dot-directory: Copilot's edit guard classifies
# hidden-path files as "sensitive" and demands per-edit confirmation.
DEFAULT_WORKSPACE = str(Path.home() / "band-e2e" / "vscode-chat-workspace")
DEFAULT_BAND_MCP_PORT = 8631


class VSCodeChatSettings(BaseSettings):
    """Env knobs for driving a real signed-in VS Code window (field == env var)."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    vscode_chat_tests_enabled: bool = False  # VSCODE_CHAT_TESTS_ENABLED
    code_command: str = "code"  # CODE_COMMAND (VS Code CLI binary override)
    band_mcp_command: str = "band-mcp"  # BAND_MCP_COMMAND (e.g. "uvx band-mcp")
    band_mcp_port: int = DEFAULT_BAND_MCP_PORT  # BAND_MCP_PORT (0 = ephemeral)
    vscode_chat_workspace: str = DEFAULT_WORKSPACE  # VSCODE_CHAT_WORKSPACE
    vscode_chat_scorecard_json: str = ""  # VSCODE_CHAT_SCORECARD_JSON ("" = no emit)
    vscode_chat_timeout: int = 300  # VSCODE_CHAT_TIMEOUT (seconds per live turn)
