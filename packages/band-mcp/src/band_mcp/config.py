"""Configuration for band-mcp.

Explicit dual credentials (`--user-key`/`--agent-key` or
`BAND_USER_KEY`/`BAND_AGENT_KEY`), `--scope` / `--tools` / `--room-id`
flags, and typo suggestions. There is no single-key fallback -- a
credential is either scope-specific or absent.

Resolution precedence per credential/field:
    CLI flag > BAND_* env

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

    `user_key` and `agent_key` are the explicit dual credentials -- there is
    no single-key fallback.

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
    warnings: list[ConfigWarning] = field(default_factory=list)


class Settings(BaseSettings):
    """Process-wide settings that are not part of the credential plumbing.

    Kept as `pydantic-settings` for backward compatibility with existing code
    paths that import `settings` directly.
    """

    # API configuration
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


def _is_explicit_empty(cli_value: str | Sequence[str] | None) -> bool:
    """True when the caller explicitly cleared a list flag (e.g. `--tools ""`).

    argparse's `action="append"` turns a bare `--tools ""` into `[""]` -- a
    one-element list holding an empty string -- never a bare `""`, so this
    checks "provided, but every token is blank" rather than testing for an
    exact string type/value (which only a direct `resolve_config(cli=...)`
    call bypassing argparse could ever produce). Applies identically to
    `--scope` and `--tools` so the two flags share one clearing contract.
    """
    if cli_value is None:
        return False
    if isinstance(cli_value, str):
        return cli_value == ""
    return len(cli_value) > 0 and all(not token.strip() for token in cli_value)


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
    it override the env/default, resolving to `[]` instead of falling through
    to the env value or default.
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


def _resolve_and_partition(
    cli_value: str | Sequence[str] | None,
    env_value: str | None,
    *,
    default: list[str],
    valid: list[str],
    flag_label: str,
    kind: ConfigWarningKind,
) -> tuple[list[str], list[ConfigWarning]]:
    """Resolve a list-valued flag (CLI > env > default) and drop unknown values.

    Shared by ``--scope`` and ``--tools``, which apply this exact sequence
    (resolve, then partition known/unknown) identically.
    """
    raw = _resolve_list(
        cli_value, env_value, default, explicit_empty=_is_explicit_empty(cli_value)
    )
    return _partition_known(raw, valid, flag_label, kind)


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
    values are dropped and surfaced in `config.warnings`.
    """
    cli = cli or {}
    env = env or {}

    # --- Credentials -------------------------------------------------------
    user_key = _resolve_scalar(cli.get("user_key"), env.get("BAND_USER_KEY"))
    agent_key = _resolve_scalar(cli.get("agent_key"), env.get("BAND_AGENT_KEY"))

    # --- Room id -----------------------------------------------------------
    room_id = _resolve_scalar(cli.get("room_id"), env.get("BAND_MCP_ROOM_ID"))

    warnings: list[ConfigWarning] = []

    # --- Scope -------------------------------------------------------------
    # Unknown values are dropped, not collapsed to []: an empty resolved
    # scope is preserved as-is, since validate() already fails loudly on an
    # empty scope, which is the right behavior when nothing could be matched.
    scope_known, scope_warnings = _resolve_and_partition(
        cli.get("scope"),
        env.get("BAND_MCP_SCOPE"),
        default=list(DEFAULT_SCOPE),
        valid=VALID_SCOPES,
        flag_label="--scope",
        kind="unknown-scope-value",
    )
    warnings.extend(scope_warnings)
    scope = [Scope(s) for s in scope_known]

    # --- Tools -------------------------------------------------------------
    tools_known, tools_warnings = _resolve_and_partition(
        cli.get("tools"),
        env.get("BAND_MCP_TOOLS"),
        default=list(DEFAULT_TOOLS),
        valid=VALID_TOOLS,
        flag_label="--tools",
        kind="unknown-tools-value",
    )
    warnings.extend(tools_warnings)
    tools = [ToolGroup(t) for t in tools_known]

    return Config(
        user_key=user_key,
        agent_key=agent_key,
        room_id=room_id,
        scope=scope,
        tools=tools,
        warnings=warnings,
    )


def validate(config: Config) -> None:
    """Fail-fast validation. Raises ConfigError if credentials are missing.

    For each scope requested in `config.scope`:
    - "agent" requires `agent_key`.
    - "human" requires `user_key`.
    """
    if not config.scope:
        raise ConfigError(
            "No valid --scope values resolved. Expected one or more of: "
            f"{', '.join(VALID_SCOPES)}."
        )

    missing: list[str] = []
    if Scope.HUMAN in config.scope and config.user_key is None:
        missing.append(
            "human scope requested but no user credential available "
            "(set --user-key / BAND_USER_KEY)"
        )
    if Scope.AGENT in config.scope and config.agent_key is None:
        missing.append(
            "agent scope requested but no agent credential available "
            "(set --agent-key / BAND_AGENT_KEY)"
        )

    if missing:
        raise ConfigError("; ".join(missing))


def resolve_credential_for_scope(config: Config, scope: Scope) -> str | None:
    """Return the API key configured for `scope`, if any."""
    match scope:
        case Scope.HUMAN:
            return config.user_key
        case Scope.AGENT:
            return config.agent_key
