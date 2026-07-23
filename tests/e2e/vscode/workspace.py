"""Scaffold the throwaway VS Code workspace the suite opens.

Pure functions (unit-tested in CI without VS Code): they only write the two
``.vscode`` config files Copilot Chat reads — the MCP server entry pointing at
the harness's band-mcp instance, and the chat settings that keep a driven run
from stalling on per-tool approval prompts.
"""

from __future__ import annotations

import json
from pathlib import Path

# Auto-answer agent questions so a driven turn never stalls on a chat-side
# question (Manage approvals docs). Deliberately NOT chat.tools.global.autoApprove:
# that is machine-wide "YOLO mode" — VS Code escalates it to a global consent
# dialog and it disables tool approval in every workspace on the host. Tool
# approvals instead rely on remembered per-tool "Always allow" choices in this
# (persistent) workspace — one click per tool, ever (see README).
AUTO_REPLY_SETTING = "chat.autoReply"

# MCP support is on by default in current VS Code; setting it explicitly keeps
# the run independent of a user-profile override ("none"). This is the current
# key — the boolean chat.mcp.enabled it replaced only works via VS Code's
# settings-migration shim.
MCP_ACCESS_SETTING = "chat.mcp.access"
MCP_ACCESS_ALL = "all"

# The one MCP server name the prompts refer to ("the band tools").
MCP_SERVER_NAME = "band"


def scaffold_workspace(root: Path, sse_url: str) -> None:
    """Write ``.vscode/mcp.json`` + ``.vscode/settings.json`` under ``root``."""
    vscode_dir = root / ".vscode"
    vscode_dir.mkdir(parents=True, exist_ok=True)

    mcp_config = {"servers": {MCP_SERVER_NAME: {"type": "sse", "url": sse_url}}}
    settings = {AUTO_REPLY_SETTING: True, MCP_ACCESS_SETTING: MCP_ACCESS_ALL}

    (vscode_dir / "mcp.json").write_text(json.dumps(mcp_config, indent=2) + "\n")
    (vscode_dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")


def workspace_marker_path(root: Path, name: str) -> Path:
    """Where a cell expects Copilot to have created ``name`` inside the workspace.

    One definition so the prompt that asks for the file and the assertion that
    checks it can never drift apart.
    """
    return root / name
