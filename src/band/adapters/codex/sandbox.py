"""Codex sandbox modes, their Windows backends, and their wire forms."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from band.integrations.codex.types import WINDOWS_SANDBOX_CONFIG_KEY

logger = logging.getLogger(__name__)


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

# thread/start.sandbox takes a kebab-case CodexSandboxMode, while
# turn/start.sandboxPolicy takes a camelCase SandboxPolicy type tag.
SANDBOX_POLICY_TYPES: dict[str, str] = {
    CodexSandboxMode.READ_ONLY: "readOnly",
    CodexSandboxMode.WORKSPACE_WRITE: "workspaceWrite",
    CodexSandboxMode.DANGER_FULL_ACCESS: "dangerFullAccess",
}


def canonical_sandbox_key(value: str) -> str:
    compact = value.strip().lower().replace("_", "-").replace(" ", "")
    return SANDBOX_KEY_ALIASES.get(compact, compact)


def normalize_sandbox_mode(sandbox: str) -> CodexSandboxMode | None:
    key = canonical_sandbox_key(sandbox)
    return CodexSandboxMode(key) if key in SANDBOX_MODES else None


def normalize_sandbox_policy(sandbox_policy: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(sandbox_policy)
    policy_type = normalized.get("type")
    if not isinstance(policy_type, str):
        return normalized
    key = canonical_sandbox_key(policy_type)
    if camel := SANDBOX_POLICY_TYPES.get(key):
        normalized["type"] = camel
    elif key == EXTERNAL_SANDBOX_KEY:
        normalized["type"] = EXTERNAL_SANDBOX_POLICY_TYPE
    return normalized


def apply_thread_sandbox(
    params: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
    windows_sandbox: CodexWindowsSandbox | None,
    effective: str | None,
) -> None:
    """Set thread/start or thread/resume's sandbox: a SandboxMode only, plus
    the Windows backend override."""
    if windows_sandbox is not None:
        params["config"] = {WINDOWS_SANDBOX_CONFIG_KEY: windows_sandbox}
    if policy is not None:
        policy_type = policy.get("type")
        if isinstance(policy_type, str) and (
            mode := normalize_sandbox_mode(policy_type)
        ):
            params["sandbox"] = mode
            return
        logger.warning(
            "sandbox_policy type %s is not representable on thread/start; "
            "it will be applied at turn level instead",
            policy_type,
        )
        return
    if effective is None:
        return
    if (mode := normalize_sandbox_mode(effective)) is not None:
        params["sandbox"] = mode
    elif canonical_sandbox_key(effective) == EXTERNAL_SANDBOX_KEY:
        logger.debug(
            "external-sandbox will be applied at turn level, not on thread/start"
        )
    else:
        logger.warning("Ignoring unsupported Codex sandbox value: %s", effective)


def apply_turn_sandbox(
    params: dict[str, Any], *, policy: dict[str, Any] | None, effective: str | None
) -> None:
    """Set turn/start's sandboxPolicy, which takes a full SandboxPolicy."""
    if policy is not None:
        params["sandboxPolicy"] = normalize_sandbox_policy(policy)
        return
    if effective is None:
        return
    if (mode := normalize_sandbox_mode(effective)) is not None:
        params["sandboxPolicy"] = {"type": SANDBOX_POLICY_TYPES[mode]}
    elif canonical_sandbox_key(effective) == EXTERNAL_SANDBOX_KEY:
        params["sandboxPolicy"] = {"type": EXTERNAL_SANDBOX_POLICY_TYPE}
    else:
        logger.warning("Ignoring unsupported Codex sandbox value: %s", effective)
