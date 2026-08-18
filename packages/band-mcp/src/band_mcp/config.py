"""Configuration for band-mcp.

This module replaces the single-key `BAND_API_KEY` + prefix
inference config with explicit dual credentials, `--scope` / `--tools` /
`--room-id` flags, and typo suggestions. The legacy `BAND_API_KEY` path is
retained as a fallback — existing deployments keep working.

Resolution precedence per credential/field:
    CLI flag > BAND_* env > BAND_API_KEY (legacy only)

`resolve_config(cli, env)` is pure — it takes a CLI-args-ish mapping and an
environment mapping, and returns a `Config`. `validate(config)` raises
`ConfigError` when credentials for a requested scope are missing. Unknown
`--scope` / `--tools` values do NOT fail startup; they are dropped from the
resolved list and surfaced as `ConfigWarning` entries in `config.warnings`.

The `Settings` model (transport, base_url, DNS rebinding) stays — only the
credential/scope/tools plumbing is new.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Mapping, Sequence, TypedDict

from pydantic_settings import BaseSettings, SettingsConfigDict


class Scope(StrEnum):
    """The two surfaces band-mcp can serve -- the CLI's `--scope` vocabulary."""

    AGENT = "agent"
    HUMAN = "human"


class ToolGroup(StrEnum):
    """Opt-in tool groups -- the CLI's `--tools` vocabulary."""

    CONTACTS = "contacts"
    MEMORY = "memory"


class Transport(StrEnum):
    """How the server talks to its client -- the CLI's `--transport` vocabulary."""

    STDIO = "stdio"
    SSE = "sse"


# Single source of truth for each closed vocabulary's valid values: derived
# from the enum above, not re-typed as a parallel list that could drift.
VALID_SCOPES: list[str] = list(Scope)
VALID_TOOLS: list[str] = list(ToolGroup)

DEFAULT_SCOPE: list[Scope] = [Scope.AGENT]
DEFAULT_TOOLS: list[ToolGroup] = []

ConfigWarningKind = Literal[
    "legacy-key-ignored",
    "unknown-scope-value",
    "unknown-tools-value",
]


class CliArgs(TypedDict, total=False):
    """The shape `resolve_config`'s `cli` parameter expects.

    Matches `_cli_mapping`'s (`server.py`) output exactly -- a concrete type
    here means every field is already narrowed to what `resolve_config`
    actually consumes, so no `isinstance` re-narrowing or `# type: ignore` is
    needed at the call sites below.
    """

    user_key: str | None
    agent_key: str | None
    room_id: str | None
    scope: str | Sequence[str] | None
    tools: str | Sequence[str] | None


class ConfigError(Exception):
    """Raised when required credentials for a requested scope are missing."""


@dataclass(frozen=True)
class ConfigWarning:
    """A non-fatal config issue surfaced at startup and logged at WARN.

    `kind` is machine-checkable; tests assert on `kind` + `did_you_mean`.
    `message` is pre-formatted for log emission; callers should not rebuild it.
    """

    kind: ConfigWarningKind
    value: str
    did_you_mean: str | None
    message: str


@dataclass(frozen=True)
class Config:
    """Resolved configuration for a single band-mcp process.

    `user_key` and `agent_key` are the explicit dual credentials. `legacy_key`
    holds `BAND_API_KEY` and is consulted ONLY as a fallback when the
    scope-specific slot is empty. Its prefix (`band_u_` / `band_a_` / `band_`)
    determines which scopes it can serve.

    `scope` / `tools` are already normalized (trimmed, lowercased, deduped,
    unknown values dropped). `warnings` captures anything that couldn't be
    honored without failing startup.
    """

    user_key: str | None = None
    agent_key: str | None = None
    room_id: str | None = None
    # Default honors ticket AC #6 ("default scope is ['agent']"). Instances
    # produced directly via `Config(user_key="x")` in tests/fixtures get the
    # same default as instances produced via `resolve_config({}, {})`.
    scope: list[Scope] = field(default_factory=lambda: list(DEFAULT_SCOPE))
    tools: list[ToolGroup] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    legacy_key: str | None = None
    warnings: list[ConfigWarning] = field(default_factory=list)


class Settings(BaseSettings):
    """Process-wide settings that are not part of the credential plumbing.

    Kept as `pydantic-settings` for backward compatibility with existing code
    paths that import `settings` directly.
    """

    # API configuration
    band_api_key: str = ""
    band_base_url: str = "https://app.band.ai"

    # Transport configuration
    transport: Transport = Transport.STDIO

    # SSE server configuration (only used when transport="sse")
    host: str = "127.0.0.1"
    port: int = 8000

    # Transport security (DNS rebinding protection)
    enable_dns_rebinding_protection: bool = True
    allowed_hosts: list[str] = []
    allowed_origins: list[str] = []

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )


settings = Settings()


# ---------------------------------------------------------------------------
# Key-prefix inference (legacy only)
# ---------------------------------------------------------------------------


def _legacy_key_capabilities(legacy_key: str | None) -> tuple[bool, bool]:
    """Return (can_serve_human, can_serve_agent) for a legacy key.

    - `band_u_...` — user key, human only.
    - `band_a_...` — agent key, agent only.
    - `band_...`   — legacy all-capable, both scopes.
    - Anything else (including None / empty) — serves neither scope.

    The thenvoi-era `thnv_*` prefixes are not recognized (INT-1096: dropped
    per user decision -- no surviving key needs the old rebrand fallback).
    """
    if not legacy_key:
        return (False, False)
    if legacy_key.startswith("band_u_"):
        return (True, False)
    if legacy_key.startswith("band_a_"):
        return (False, True)
    if legacy_key.startswith("band_"):
        return (True, True)
    return (False, False)


# ---------------------------------------------------------------------------
# Typo suggestions
# ---------------------------------------------------------------------------


def _suggest_value(bad: str, valid: list[str]) -> str | None:
    """Return the closest match in `valid` or None.

    Thin wrapper over `difflib.get_close_matches(bad, valid, n=1, cutoff=0.6)`.
    Private to `config.py` on purpose — the registrar doesn't need it.
    """
    matches = difflib.get_close_matches(bad, valid, n=1, cutoff=0.6)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# List-value parsing (shared by --scope and --tools)
# ---------------------------------------------------------------------------


def _normalize_list_value(raw: str | Sequence[str] | None) -> list[str]:
    """Normalize a CLI/env list value into a clean list of lowercased tokens.

    Accepts:
    - None -> []
    - "" -> []
    - "a,b" -> ["a", "b"]
    - ["a", "b,c"] -> ["a", "b", "c"]  (supports both repeatable and CSV forms)

    Trims whitespace, lowercases, drops empty tokens, preserves order, dedupes.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    else:
        parts = []
        for entry in raw:
            parts.extend(entry.split(","))

    seen: set[str] = set()
    out: list[str] = []
    for token in parts:
        clean = token.strip().lower()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _resolve_list(
    cli_value: str | Sequence[str] | None,
    env_value: str | None,
    default: list[str],
    *,
    explicit_empty: bool,
) -> list[str]:
    """Apply per-field precedence for list-valued settings.

    Precedence: CLI > BAND_* env > default.

    `explicit_empty` lets a caller pass `--tools ""` (empty CLI value) and have
    it override the env/default, matching the ticket's `--tools ""` -> []
    requirement.
    """
    if explicit_empty:
        return []
    if cli_value is not None and (
        not isinstance(cli_value, (list, tuple)) or len(cli_value) > 0
    ):
        return _normalize_list_value(cli_value)
    if env_value is not None:
        return _normalize_list_value(env_value)
    return list(default)


def _partition_known(
    raw: list[str],
    valid: list[str],
    flag_label: str,
    kind: ConfigWarningKind,
) -> tuple[list[str], list[ConfigWarning]]:
    """Split `raw` into (known, warnings). Unknown values drop + warn.

    `flag_label` is the human-facing flag name used in warning messages
    (e.g. `--tools`, `--scope`).
    """
    known: list[str] = []
    warnings: list[ConfigWarning] = []
    valid_set = set(valid)
    for value in raw:
        if value in valid_set:
            known.append(value)
            continue
        suggestion = _suggest_value(value, valid)
        if suggestion is not None:
            msg = (
                f"unknown {flag_label} value '{value}' — "
                f"did you mean '{suggestion}'? ignoring."
            )
        else:
            msg = (
                f"unknown {flag_label} value '{value}' — "
                f"valid values: {', '.join(valid)}. ignoring."
            )
        warnings.append(
            ConfigWarning(
                kind=kind,
                value=value,
                did_you_mean=suggestion,
                message=msg,
            )
        )
    return known, warnings


# ---------------------------------------------------------------------------
# Per-slot precedence for scalar values
# ---------------------------------------------------------------------------


def _resolve_scalar(
    cli_value: str | None,
    env_value: str | None,
) -> str | None:
    """CLI > BAND_* > None. Empty strings count as unset."""
    for candidate in (cli_value, env_value):
        if candidate is not None and candidate != "":
            return candidate
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_config(
    cli: CliArgs | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Resolve a `Config` from CLI args and environment.

    `cli` keys (all optional, see `CliArgs`): `user_key`, `agent_key`,
    `room_id`, `scope`, `tools`. Values are what argparse produces. For
    `scope` / `tools`, accept either a comma-separated string or a list of
    strings (argparse `append` action).

    `env` is typically `os.environ`. Anything not supplied is treated as unset.

    The returned `Config` is already normalized: unknown `--scope` / `--tools`
    values are dropped and surfaced in `config.warnings`, and cross-slot
    legacy-key masking is resolved.
    """
    cli = cli or {}
    env = env or {}

    # --- Credentials -------------------------------------------------------
    user_key = _resolve_scalar(cli.get("user_key"), env.get("BAND_USER_KEY"))
    agent_key = _resolve_scalar(cli.get("agent_key"), env.get("BAND_AGENT_KEY"))
    legacy_key_raw = env.get("BAND_API_KEY")
    legacy_key: str | None = legacy_key_raw if legacy_key_raw else None

    # --- Room id -----------------------------------------------------------
    room_id = _resolve_scalar(cli.get("room_id"), env.get("BAND_MCP_ROOM_ID"))

    warnings: list[ConfigWarning] = []

    # --- Scope -------------------------------------------------------------
    cli_scope = cli.get("scope")
    scope_raw = _resolve_list(
        cli_scope,
        env.get("BAND_MCP_SCOPE"),
        default=list(DEFAULT_SCOPE),
        explicit_empty=False,
    )
    scope_known, scope_warnings = _partition_known(
        scope_raw, VALID_SCOPES, "--scope", "unknown-scope-value"
    )
    warnings.extend(scope_warnings)
    # If every caller-supplied value was unknown, fall back to the default.
    # The ticket requires unknown values to be dropped, not to collapse scope
    # to []; an empty resolved scope would also trigger validate() to fail
    # loudly, which is the right behavior when the operator typed something
    # that could not be matched at all. Prefer explicit (possibly empty) user
    # intent over a silent default here.
    scope = [Scope(s) for s in scope_known]

    # --- Tools -------------------------------------------------------------
    cli_tools = cli.get("tools")
    # `--tools ""` should produce []: detect that here. An empty string from
    # argparse (default=None) signals the operator explicitly cleared the list.
    explicit_empty = isinstance(cli_tools, str) and cli_tools == ""
    tools_raw = _resolve_list(
        cli_tools,
        env.get("BAND_MCP_TOOLS"),
        default=list(DEFAULT_TOOLS),
        explicit_empty=explicit_empty,
    )
    tools_known, tools_warnings = _partition_known(
        tools_raw, VALID_TOOLS, "--tools", "unknown-tools-value"
    )
    warnings.extend(tools_warnings)
    tools = [ToolGroup(t) for t in tools_known]

    # --- Cross-slot legacy-key masking ------------------------------------
    # If a scope-specific key is set AND legacy_key is populated, the legacy
    # key is ignored for that scope. Emit a warning if legacy_key would have
    # been consulted but is now ignored. We only warn once per process; the
    # value of `value` is the semantic slot label ("legacy_key") so tests can
    # assert on it deterministically.
    if legacy_key is not None:
        legacy_human, legacy_agent = _legacy_key_capabilities(legacy_key)
        # A legacy key is "ignored" when BOTH of these hold:
        #   - the scope-specific slot that would otherwise have been filled
        #     from it is already populated, AND
        #   - that scope-specific slot would have been served by legacy_key.
        # Put differently: if user_key is set AND legacy_key could serve human,
        # legacy's human role is masked. Same for agent.
        human_masked = user_key is not None and legacy_human
        agent_masked = agent_key is not None and legacy_agent
        if human_masked or agent_masked:
            warnings.append(
                ConfigWarning(
                    kind="legacy-key-ignored",
                    value="legacy_key",
                    did_you_mean=None,
                    message=(
                        "BAND_API_KEY is set but scope-specific keys "
                        "(BAND_USER_KEY / BAND_AGENT_KEY) take precedence; "
                        "legacy key ignored for overlapping scope(s)."
                    ),
                )
            )

    return Config(
        user_key=user_key,
        agent_key=agent_key,
        room_id=room_id,
        scope=scope,
        tools=tools,
        legacy_key=legacy_key,
        warnings=warnings,
    )


def validate(config: Config) -> None:
    """Fail-fast validation. Raises ConfigError if credentials are missing.

    For each scope requested in `config.scope`:
    - "agent" requires `agent_key` OR an agent-capable `legacy_key`.
    - "human" requires `user_key` OR a human-capable `legacy_key`.
    """
    if not config.scope:
        raise ConfigError(
            "No valid --scope values resolved. Expected one or more of: "
            f"{', '.join(VALID_SCOPES)}."
        )

    legacy_human, legacy_agent = _legacy_key_capabilities(config.legacy_key)

    missing: list[str] = []
    if Scope.HUMAN in config.scope:
        if config.user_key is None and not legacy_human:
            missing.append(
                "human scope requested but no user credential available "
                "(set --user-key / BAND_USER_KEY, or use a "
                "human-capable BAND_API_KEY)"
            )
    if Scope.AGENT in config.scope:
        if config.agent_key is None and not legacy_agent:
            missing.append(
                "agent scope requested but no agent credential available "
                "(set --agent-key / BAND_AGENT_KEY, or use an "
                "agent-capable BAND_API_KEY)"
            )

    if missing:
        raise ConfigError("; ".join(missing))


def resolve_credential_for_scope(config: Config, scope: Scope) -> str | None:
    """Return the API key that should be used for `scope`.

    Scope-specific key wins; legacy key is a fallback. Returns None if nothing
    serves the scope (validate() would have raised earlier).
    """
    match scope:
        case Scope.HUMAN:
            if config.user_key is not None:
                return config.user_key
            legacy_human, _ = _legacy_key_capabilities(config.legacy_key)
            return config.legacy_key if legacy_human else None
        case Scope.AGENT:
            if config.agent_key is not None:
                return config.agent_key
            _, legacy_agent = _legacy_key_capabilities(config.legacy_key)
            return config.legacy_key if legacy_agent else None
