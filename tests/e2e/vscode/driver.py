"""Drive Copilot Chat in a real VS Code window through the ``code chat`` CLI.

The surface has no inbound channel: Copilot in VS Code cannot be pushed a Band
room message (band-mcp exposes no message-read tool either), so every turn is
driver-initiated — the prompt relays the triggering room message and instructs
Copilot to act through the band MCP tools. This mirrors real usage, where the
developer relays context into chat and Copilot posts to Band.

``PromptDriver`` is the seam: assertions never touch VS Code (they are all
Band-side), so a future ``@vscode/test-electron`` backend only has to submit
prompts to slot in.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from tests.e2e.vscode.workspace import MCP_SERVER_NAME

logger = logging.getLogger(__name__)

# `code chat` submits and returns; the window processes the turn asynchronously.
# This bounds only the CLI handoff, not the model turn (Band-side waits do that).
SUBMIT_TIMEOUT_S = 60

# The `code chat` CLI has no verified per-invocation new-session switch, so a
# fresh-session request is expressed in the prompt itself. Weaker than a real
# session reset — the runbook documents the manual "New Chat" variant.
FRESH_SESSION_PREAMBLE = (
    "Start from a clean slate: ignore everything discussed earlier in this "
    "chat and rely only on this message and your tools."
)


class PreflightError(RuntimeError):
    """The VS Code CLI is missing or too old to drive; message points at the runbook."""


class PromptDriver(Protocol):
    """Submit one agent-mode prompt to the surface under test."""

    async def submit_prompt(self, prompt: str, *, new_session: bool = False) -> None:
        """Send ``prompt`` to Copilot Chat; ``new_session`` requests a fresh context."""
        ...


def turn_prompt(
    chat_id: str,
    agent_name: str,
    *,
    sender_name: str,
    message: str,
    instruction: str,
) -> str:
    """The one prompt shape every cell submits.

    Relays the room message (the surface cannot read it back) and pins the
    room id, since every band tool call takes an explicit ``chat_id``.
    """
    return (
        f"You are the Band platform agent '{agent_name}'. You participate in "
        f"Band chat rooms through the MCP tools of the '{MCP_SERVER_NAME}' "
        f"server (band_send_message, band_get_participants, band_store_memory, "
        f"band_list_memories, ...). Every band tool call must pass "
        f"chat_id='{chat_id}'.\n\n"
        f"New message in the room from {sender_name}:\n"
        f"---\n{message}\n---\n\n"
        f"{instruction}\n"
        f"Always deliver your answer into the room with band_send_message, "
        f"mentioning {sender_name}."
    )


# Seconds after opening the workspace for the window, extension host, and MCP
# client to come up before the first prompt is submitted.
WINDOW_OPEN_GRACE_S = 20


class CodeChatDriver:
    """Submit prompts via ``code chat -m agent`` against the workspace's window.

    Construct through :func:`vscode_window`, which owns the ready-to-drive
    lifecycle (preflight -> open -> readiness grace).
    """

    def __init__(self, code_command: list[str], workspace: Path) -> None:
        self._code_command = code_command
        self._workspace = workspace

    async def submit_prompt(self, prompt: str, *, new_session: bool = False) -> None:
        if new_session:
            prompt = f"{FRESH_SESSION_PREAMBLE}\n\n{prompt}"
        await self._run("chat", "-m", "agent", prompt)

    async def _run(self, *args: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *self._code_command,
            *args,
            cwd=self._workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(
            process.communicate(), timeout=SUBMIT_TIMEOUT_S
        )
        if output:
            logger.debug("code %s: %s", args[0], output.decode(errors="replace"))
        if process.returncode != 0:
            raise RuntimeError(
                f"code {args[0]} exited with {process.returncode}: "
                f"{output.decode(errors='replace').strip()}"
            )


@asynccontextmanager
async def vscode_window(
    code_command: list[str], workspace: Path
) -> AsyncIterator[CodeChatDriver]:
    """A ready-to-drive VS Code window on ``workspace``.

    Entry owns the whole becoming-drivable flow: preflight the CLI (raises
    :class:`PreflightError` with the runbook pointer), open the workspace
    window, and wait out the startup grace so the extension host and MCP
    client are up before the first prompt. Opened deliberately *without*
    ``--disable-workspace-trust``: Copilot's AI features require a trusted
    workspace, so an untrusted launch only defers the dialog to chat time —
    the stable workspace path makes "Trust Folder & Continue" a one-time click.

    Exit is a no-op by contract: the window is human-owned (the human signed
    in to Copilot there) and stays open across runs.
    """
    preflight(code_command)
    driver = CodeChatDriver(code_command, workspace)
    await driver._run(str(workspace))
    await asyncio.sleep(WINDOW_OPEN_GRACE_S)
    yield driver


Runner = Callable[[list[str]], str]


def _default_runner(command: list[str]) -> str:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=SUBMIT_TIMEOUT_S, check=True
    ).stdout


def preflight(code_command: list[str], *, run: Runner = _default_runner) -> None:
    """Fail fast (with the runbook pointer) when the VS Code CLI cannot drive chat."""
    hint = "see tests/e2e/vscode/README.md for setup"
    if shutil.which(code_command[0]) is None:
        raise PreflightError(f"'{code_command[0]}' not found on PATH — {hint}")
    try:
        run([*code_command, "--version"])
        run([*code_command, "chat", "--help"])
    except (subprocess.SubprocessError, OSError) as error:
        raise PreflightError(
            f"VS Code CLI cannot drive chat ({error}) — {hint}"
        ) from error


def capture_versions(
    code_command: list[str],
    band_mcp_command: list[str],
    *,
    run: Runner = _default_runner,
) -> dict[str, str]:
    """The environment evidence the scorecard sidecar records for a live run."""

    def capture(command: list[str]) -> str:
        try:
            return run(command).strip()
        except (subprocess.SubprocessError, OSError) as error:
            return f"unavailable ({error})"

    extensions = capture([*code_command, "--list-extensions", "--show-versions"])
    copilot_lines = [
        line for line in extensions.splitlines() if "copilot" in line.lower()
    ]
    # Current VS Code bundles Copilot Chat, which --list-extensions omits; its
    # version is then pinned by the VS Code build recorded above.
    copilot = "; ".join(copilot_lines) or "built-in (bundled with this VS Code build)"
    return {
        "os": platform.platform(),
        "vscode": capture([*code_command, "--version"]).replace("\n", " "),
        "copilot_extensions": copilot,
        "band_mcp": capture([*band_mcp_command, "--version"]),
    }
