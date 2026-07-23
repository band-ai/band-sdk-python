"""Scaffold the throwaway VS Code workspace the suite opens.

Pure functions (unit-tested in CI without VS Code): they only write the two
``.vscode`` config files Copilot Chat reads — the MCP server entry pointing at
the harness's band-mcp instance, and the chat settings that keep a driven run
from stalling on per-tool approval prompts.
"""

from __future__ import annotations

import json
from pathlib import Path

# VS Code's tool auto-approval knob (Manage approvals docs, VS Code 1.103+).
# Security trade-off is deliberate and bounded: the workspace is a throwaway
# temp dir and the only MCP server is the harness's own band-mcp. If the
# installed VS Code rejects the key (or scopes it user-level only), the runbook
# fallback is one manual "Always allow" click per tool on the first turn.
AUTO_APPROVE_SETTING = "chat.tools.global.autoApprove"

# MCP support is on by default in current VS Code; setting it explicitly keeps
# the run independent of a user-profile override. Unknown keys are ignored.
MCP_ENABLED_SETTING = "chat.mcp.enabled"

# The one MCP server name the prompts refer to ("the band tools").
MCP_SERVER_NAME = "band"


def scaffold_workspace(root: Path, sse_url: str) -> None:
    """Write ``.vscode/mcp.json`` + ``.vscode/settings.json`` under ``root``."""
    vscode_dir = root / ".vscode"
    vscode_dir.mkdir(parents=True, exist_ok=True)

    mcp_config = {"servers": {MCP_SERVER_NAME: {"type": "sse", "url": sse_url}}}
    settings = {AUTO_APPROVE_SETTING: True, MCP_ENABLED_SETTING: True}

    (vscode_dir / "mcp.json").write_text(json.dumps(mcp_config, indent=2) + "\n")
    (vscode_dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")


def workspace_marker_path(root: Path, name: str) -> Path:
    """Where a cell expects Copilot to have created ``name`` inside the workspace.

    One definition so the prompt that asks for the file and the assertion that
    checks it can never drift apart.
    """
    return root / name
