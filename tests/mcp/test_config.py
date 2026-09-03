"""Unit tests for `band_mcp.config`.

Covers config resolution's acceptance criteria:
- Precedence per slot: CLI > BAND_* env. There is no single-key fallback.
- `--scope` / `--tools` parsing (comma-separated, repeatable, explicit empty).
- Unknown values produce warnings with `did_you_mean` and are dropped.
- `validate()` fail-fast per scope/credential.
- `room_id` resolution.
- `ConfigWarning` dataclass shape.
"""

from __future__ import annotations

import dataclasses

import pytest

from band_mcp.config import (
    Config,
    ConfigError,
    ConfigWarning,
    _suggest_value,
    resolve_config,
    resolve_credential_for_scope,
    validate,
)


def _warning_of_kind(cfg: Config, kind: str) -> ConfigWarning:
    """The one warning of `kind` on `cfg` -- fails loudly if there isn't
    exactly one, since every caller here expects a single match."""
    matches = [w for w in cfg.warnings if w.kind == kind]
    assert len(matches) == 1, f"expected exactly one {kind!r} warning, got {matches!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


def test_config_warning_is_frozen_dataclass():
    w = ConfigWarning(
        kind="unknown-tools-value",
        value="contact",
        did_you_mean="contacts",
        message="msg",
    )
    assert dataclasses.is_dataclass(w)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.kind = "unknown-scope-value"  # type: ignore[misc]


def test_config_warning_fields():
    fields = {f.name for f in dataclasses.fields(ConfigWarning)}
    assert fields == {"kind", "value", "did_you_mean", "message"}


def test_config_is_frozen_dataclass():
    cfg = Config()
    assert dataclasses.is_dataclass(cfg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.scope = ["human"]  # type: ignore[misc]


def test_config_default_scope_is_agent():
    # AC #6: default scope is ["agent"]. A bare `Config()` must honor it so
    # test fixtures and external callers don't silently fail validate().
    cfg = Config()
    assert cfg.scope == ["agent"]
    assert cfg.tools == []


def test_config_default_scope_isolated_between_instances():
    # Guard against the classic mutable-default-argument bug.
    a = Config()
    b = Config()
    a.scope.append("human")
    assert b.scope == ["agent"]


# ---------------------------------------------------------------------------
# _suggest_value
# ---------------------------------------------------------------------------


def test_suggest_value_close_match():
    assert _suggest_value("contact", ["contacts", "memory"]) == "contacts"
    assert _suggest_value("huamn", ["agent", "human"]) == "human"
    assert _suggest_value("agnet", ["agent", "human"]) == "agent"


def test_suggest_value_no_match():
    assert _suggest_value("zzz", ["contacts", "memory"]) is None


# ---------------------------------------------------------------------------
# Credential precedence per slot
# ---------------------------------------------------------------------------


def test_user_key_cli_beats_env():
    cfg = resolve_config(
        cli={"user_key": "cli_user"},
        env={"BAND_USER_KEY": "env_band"},
    )
    assert cfg.user_key == "cli_user"


def test_user_key_band_when_only_band_set():
    cfg = resolve_config(cli={}, env={"BAND_USER_KEY": "band_only"})
    assert cfg.user_key == "band_only"


def test_user_key_none_when_nothing_set():
    cfg = resolve_config(cli={}, env={})
    assert cfg.user_key is None


def test_agent_key_precedence_chain():
    # CLI beats BAND_*
    cfg = resolve_config(
        cli={"agent_key": "cli_a"},
        env={"BAND_AGENT_KEY": "env_b"},
    )
    assert cfg.agent_key == "cli_a"

    cfg = resolve_config(cli={}, env={"BAND_AGENT_KEY": "env_b"})
    assert cfg.agent_key == "env_b"


def test_resolve_credential_for_scope_returns_scope_specific_key():
    cfg = resolve_config(
        cli={}, env={"BAND_USER_KEY": "user_1", "BAND_AGENT_KEY": "agent_1"}
    )
    assert resolve_credential_for_scope(cfg, "human") == "user_1"
    assert resolve_credential_for_scope(cfg, "agent") == "agent_1"


def test_resolve_credential_for_scope_returns_none_when_unset():
    cfg = resolve_config(cli={}, env={})
    assert resolve_credential_for_scope(cfg, "human") is None
    assert resolve_credential_for_scope(cfg, "agent") is None


# ---------------------------------------------------------------------------
# Room id
# ---------------------------------------------------------------------------


def test_room_id_precedence():
    cfg = resolve_config(
        cli={"room_id": "cli_room"},
        env={"BAND_MCP_ROOM_ID": "env_b"},
    )
    assert cfg.room_id == "cli_room"

    cfg = resolve_config(cli={}, env={"BAND_MCP_ROOM_ID": "env_b"})
    assert cfg.room_id == "env_b"


def test_room_id_defaults_none():
    cfg = resolve_config(cli={}, env={})
    assert cfg.room_id is None


# ---------------------------------------------------------------------------
# --scope parsing
# ---------------------------------------------------------------------------


def test_scope_default_is_agent():
    cfg = resolve_config(cli={}, env={})
    assert cfg.scope == ["agent"]


def test_scope_comma_separated():
    cfg = resolve_config(cli={"scope": "agent,human"}, env={})
    assert cfg.scope == ["agent", "human"]


def test_scope_repeatable_list():
    cfg = resolve_config(cli={"scope": ["agent", "human"]}, env={})
    assert cfg.scope == ["agent", "human"]


def test_scope_repeatable_mixed_with_csv():
    cfg = resolve_config(cli={"scope": ["agent,human", "agent"]}, env={})
    # de-duped, order preserved
    assert cfg.scope == ["agent", "human"]


def test_scope_precedence_cli_over_env():
    cfg = resolve_config(
        cli={"scope": "human"},
        env={"BAND_MCP_SCOPE": "agent,human"},
    )
    assert cfg.scope == ["human"]


def test_scope_band_env():
    cfg = resolve_config(cli={}, env={"BAND_MCP_SCOPE": "human"})
    assert cfg.scope == ["human"]


def test_scope_unknown_value_warned_and_dropped():
    cfg = resolve_config(cli={"scope": "agent,agnet"}, env={})
    assert cfg.scope == ["agent"]
    warn = _warning_of_kind(cfg, "unknown-scope-value")
    assert warn.value == "agnet"
    assert warn.did_you_mean == "agent"


def test_scope_unknown_huamn_suggests_human():
    cfg = resolve_config(cli={"scope": "huamn"}, env={})
    assert _warning_of_kind(cfg, "unknown-scope-value").did_you_mean == "human"


# ---------------------------------------------------------------------------
# --tools parsing
# ---------------------------------------------------------------------------


def test_tools_default_empty():
    cfg = resolve_config(cli={}, env={})
    assert cfg.tools == []


def test_tools_comma_separated():
    cfg = resolve_config(cli={"tools": "contacts,memory"}, env={})
    assert cfg.tools == ["contacts", "memory"]


def test_tools_repeatable():
    cfg = resolve_config(cli={"tools": ["contacts", "memory"]}, env={})
    assert cfg.tools == ["contacts", "memory"]


def test_tools_explicit_empty_string_overrides_env():
    cfg = resolve_config(cli={"tools": ""}, env={"BAND_MCP_TOOLS": "contacts"})
    assert cfg.tools == []


def test_tools_precedence():
    cfg = resolve_config(
        cli={"tools": "memory"},
        env={"BAND_MCP_TOOLS": "contacts,memory"},
    )
    assert cfg.tools == ["memory"]

    cfg = resolve_config(cli={}, env={"BAND_MCP_TOOLS": "memory"})
    assert cfg.tools == ["memory"]


def test_tools_unknown_value_with_suggestion():
    cfg = resolve_config(cli={"tools": "contact"}, env={})
    assert cfg.tools == []
    warn = _warning_of_kind(cfg, "unknown-tools-value")
    assert warn.value == "contact"
    assert warn.did_you_mean == "contacts"


def test_tools_unknown_value_no_suggestion():
    cfg = resolve_config(cli={"tools": "zzz"}, env={})
    warn = _warning_of_kind(cfg, "unknown-tools-value")
    assert warn.value == "zzz"
    assert warn.did_you_mean is None


def test_tools_known_and_unknown_mixed():
    cfg = resolve_config(cli={"tools": "contacts,zzz,memory"}, env={})
    assert cfg.tools == ["contacts", "memory"]
    assert _warning_of_kind(cfg, "unknown-tools-value").value == "zzz"


def test_tools_comma_separated_with_tasks():
    cfg = resolve_config(cli={"tools": "contacts,memory,tasks"}, env={})
    assert cfg.tools == ["contacts", "memory", "tasks"]


def test_tools_known_and_unknown_mixed_with_tasks():
    cfg = resolve_config(cli={"tools": "contacts,zzz,memory,tasks"}, env={})
    assert cfg.tools == ["contacts", "memory", "tasks"]
    assert _warning_of_kind(cfg, "unknown-tools-value").value == "zzz"


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_passes_with_agent_key_agent_scope():
    cfg = resolve_config(cli={"agent_key": "band_a_1"}, env={})
    # Default scope is ["agent"]; agent_key set -> ok
    validate(cfg)


def test_validate_fails_agent_scope_missing_agent_key():
    cfg = resolve_config(cli={}, env={})
    with pytest.raises(ConfigError, match="agent scope requested"):
        validate(cfg)


def test_validate_fails_human_scope_missing_user_key():
    cfg = resolve_config(cli={"scope": "human", "agent_key": "band_a_1"}, env={})
    with pytest.raises(ConfigError, match="human scope requested"):
        validate(cfg)


def test_validate_passes_human_scope_with_user_key():
    cfg = resolve_config(cli={"scope": "human", "user_key": "band_u_1"}, env={})
    validate(cfg)


def test_validate_fails_on_empty_scope():
    # Only unknown scope values → resolved scope is empty → validate fails.
    cfg = resolve_config(cli={"scope": "zzzzz"}, env={})
    # Defensive: empty scope should raise, since no scope means "serve nothing".
    with pytest.raises(ConfigError, match="No valid --scope values resolved"):
        validate(cfg)


# ---------------------------------------------------------------------------
# Full Config shape
# ---------------------------------------------------------------------------


def test_config_has_expected_fields():
    fields = {f.name for f in dataclasses.fields(Config)}
    assert fields == {
        "user_key",
        "agent_key",
        "room_id",
        "scope",
        "tools",
        "warnings",
    }


def test_config_full_resolution_example():
    cfg = resolve_config(
        cli={
            "user_key": "band_u_cli",
            "agent_key": "band_a_cli",
            "room_id": "r_cli",
            "scope": "agent,human",
            "tools": "contacts,memory",
        },
        env={},
    )
    assert cfg.user_key == "band_u_cli"
    assert cfg.agent_key == "band_a_cli"
    assert cfg.room_id == "r_cli"
    assert cfg.scope == ["agent", "human"]
    assert cfg.tools == ["contacts", "memory"]
    assert cfg.warnings == []
    validate(cfg)  # must not raise


# ---------------------------------------------------------------------------
# Warning message format (sanity)
# ---------------------------------------------------------------------------


def test_unknown_tools_warning_message_includes_suggestion():
    cfg = resolve_config(cli={"tools": "contact"}, env={})
    warn = _warning_of_kind(cfg, "unknown-tools-value")
    assert "did you mean 'contacts'" in warn.message
    assert "'contact'" in warn.message


def test_unknown_tools_warning_message_lists_valid_when_no_suggestion():
    cfg = resolve_config(cli={"tools": "zzz"}, env={})
    warn = _warning_of_kind(cfg, "unknown-tools-value")
    assert "contacts" in warn.message
    assert "memory" in warn.message
