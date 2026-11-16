"""Codex sandbox modes and their Windows backends."""

from __future__ import annotations

from enum import StrEnum


class CodexSandboxMode(StrEnum):
    """thread/start's kebab-case ``SandboxMode`` values -- also exactly what
    ``/sandbox`` accepts."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class CodexWindowsSandbox(StrEnum):
    """Codex's ``[windows] sandbox`` backends: without one, a Windows app-server
    cannot enforce ``read-only`` or ``workspace-write``."""

    ELEVATED = "elevated"
    UNELEVATED = "unelevated"


# Membership by plain string: Python 3.11's Enum rejects `"word" in CodexSandboxMode`.
SANDBOX_MODES = frozenset(CodexSandboxMode)


# Sandboxes Codex enforces itself; on Windows they need a Windows sandbox backend.
ENFORCED_SANDBOX_MODES = frozenset(
    {CodexSandboxMode.READ_ONLY, CodexSandboxMode.WORKSPACE_WRITE}
)


# A sandbox kind expressible only as a turn/start sandboxPolicy, not a mode.
EXTERNAL_SANDBOX_KEY = "external-sandbox"


EXTERNAL_SANDBOX_POLICY_TYPE = "externalSandbox"


# Config spellings accepted without the dashes, e.g. "readonly".
SANDBOX_KEY_ALIASES = {
    key.replace("-", ""): key for key in (*CodexSandboxMode, EXTERNAL_SANDBOX_KEY)
}


SANDBOX_CONFIRM_FLAG = "--confirm"


SANDBOX_MODE_CHOICES = "|".join(CodexSandboxMode)


WINDOWS_SANDBOX_MISSING_WARNING = (
    "Codex on Windows has no sandbox configured, so it cannot enforce a "
    "read-only or workspace-write sandbox: with approval_policy 'never' it "
    "refuses every shell command and file edit, and otherwise it asks approval "
    "for each. Set windows_sandbox (or CODEX_WINDOWS_SANDBOX) to "
    f"'{CodexWindowsSandbox.UNELEVATED}' or '{CodexWindowsSandbox.ELEVATED}', "
    "or set [windows] sandbox in the Codex config."
)


WINDOWS_SANDBOX_SETUP_WARNING = (
    f"Codex's {CodexWindowsSandbox.ELEVATED} Windows sandbox has not finished "
    "its setup, so sandboxed commands fail. Complete the setup in Codex, or set "
    f"windows_sandbox (or CODEX_WINDOWS_SANDBOX) to '{CodexWindowsSandbox.UNELEVATED}'."
)
