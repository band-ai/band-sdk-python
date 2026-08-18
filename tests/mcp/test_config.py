"""Unit tests for `band_mcp.config`.

Covers Phase 2 (INT-350) acceptance criteria:
- Precedence per slot: CLI > BAND_* > BAND_API_KEY (legacy only).
- Scope-specific key wins; legacy is fallback + emits warning when masked.
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
        w.kind = "legacy-key-ignored"  # type: ignore[misc]


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


def test_legacy_key_only_from_band_api_key():
    cfg = resolve_config(cli={}, env={"BAND_API_KEY": "band_u_abc"})
    assert cfg.legacy_key == "band_u_abc"
    # legacy doesn't populate user_key/agent_key directly
    assert cfg.user_key is None
    assert cfg.agent_key is None


# ---------------------------------------------------------------------------
# Cross-slot precedence (legacy masking)
# ---------------------------------------------------------------------------


def test_user_key_masks_legacy_human_capable():
    cfg = resolve_config(
        cli={}, env={"BAND_USER_KEY": "user_1", "BAND_API_KEY": "thnv_u_xxx"}
    )
    # user_key populated; for human, user_key wins
    assert resolve_credential_for_scope(cfg, "human") == "user_1"
    # legacy ignored warning emitted
    kinds = [w.kind for w in cfg.warnings]
    assert "legacy-key-ignored" in kinds


def test_agent_key_masks_legacy_all_capable():
    cfg = resolve_config(
        cli={}, env={"BAND_AGENT_KEY": "agent_1", "BAND_API_KEY": "thnv_abc"}
    )
    # agent_key wins for agent scope
    assert resolve_credential_for_scope(cfg, "agent") == "agent_1"
    # Legacy is all-capable → it's masked for agent; still emits warning.
    assert any(w.kind == "legacy-key-ignored" for w in cfg.warnings)
    # Legacy still usable as fallback for human (user_key not set).
    assert resolve_credential_for_scope(cfg, "human") == "thnv_abc"


def test_no_legacy_warning_when_no_overlap():
    # legacy_key is agent-only (thnv_a_) and only user_key is set → no overlap,
    # no warning.
    cfg = resolve_config(
        cli={}, env={"BAND_USER_KEY": "user_1", "BAND_API_KEY": "thnv_a_xxx"}
    )
    assert all(w.kind != "legacy-key-ignored" for w in cfg.warnings)


def test_legacy_fallback_when_scope_key_empty():
    cfg = resolve_config(cli={}, env={"BAND_API_KEY": "thnv_abc"})
    assert resolve_credential_for_scope(cfg, "human") == "thnv_abc"
    assert resolve_credential_for_scope(cfg, "agent") == "thnv_abc"


@pytest.mark.parametrize(
    ("legacy_key", "expected_human", "expected_agent"),
    [
        ("band_u_abc", True, False),
        ("band_a_abc", False, True),
        ("band_abc", True, True),
    ],
)
def test_band_prefixed_legacy_key_capabilities(
    legacy_key: str, expected_human: bool, expected_agent: bool
) -> None:
    cfg = resolve_config(cli={}, env={"BAND_API_KEY": legacy_key})

    if expected_human:
        assert resolve_credential_for_scope(cfg, "human") == legacy_key
    else:
        assert resolve_credential_for_scope(cfg, "human") is None

    if expected_agent:
        assert resolve_credential_for_scope(cfg, "agent") == legacy_key
    else:
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
    warns = [w for w in cfg.warnings if w.kind == "unknown-scope-value"]
    assert len(warns) == 1
    assert warns[0].value == "agnet"
    assert warns[0].did_you_mean == "agent"


def test_scope_unknown_huamn_suggests_human():
    cfg = resolve_config(cli={"scope": "huamn"}, env={})
    warns = [w for w in cfg.warnings if w.kind == "unknown-scope-value"]
    assert warns[0].did_you_mean == "human"


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
    warns = [w for w in cfg.warnings if w.kind == "unknown-tools-value"]
    assert len(warns) == 1
    assert warns[0].value == "contact"
    assert warns[0].did_you_mean == "contacts"


def test_tools_unknown_value_no_suggestion():
    cfg = resolve_config(cli={"tools": "zzz"}, env={})
    warns = [w for w in cfg.warnings if w.kind == "unknown-tools-value"]
    assert len(warns) == 1
    assert warns[0].value == "zzz"
    assert warns[0].did_you_mean is None


def test_tools_known_and_unknown_mixed():
    cfg = resolve_config(cli={"tools": "contacts,zzz,memory"}, env={})
    assert cfg.tools == ["contacts", "memory"]
    assert any(
        w.kind == "unknown-tools-value" and w.value == "zzz" for w in cfg.warnings
    )


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_passes_with_agent_key_agent_scope():
    cfg = resolve_config(cli={"agent_key": "thnv_a_1"}, env={})
    # Default scope is ["agent"]; agent_key set -> ok
    validate(cfg)


def test_validate_fails_agent_scope_missing_agent_key():
    cfg = resolve_config(cli={}, env={})
    with pytest.raises(ConfigError):
        validate(cfg)


def test_validate_fails_human_scope_missing_user_key():
    cfg = resolve_config(cli={"scope": "human", "agent_key": "thnv_a_1"}, env={})
    with pytest.raises(ConfigError):
        validate(cfg)


def test_validate_passes_human_scope_with_user_key():
    cfg = resolve_config(cli={"scope": "human", "user_key": "thnv_u_1"}, env={})
    validate(cfg)


def test_validate_passes_via_legacy_key_agent_capable():
    # thnv_a_ legacy satisfies agent scope
    cfg = resolve_config(cli={}, env={"BAND_API_KEY": "thnv_a_xyz"})
    validate(cfg)


def test_validate_passes_via_legacy_key_all_capable_both_scopes():
    cfg = resolve_config(cli={"scope": "agent,human"}, env={"BAND_API_KEY": "thnv_xyz"})
    validate(cfg)


def test_validate_fails_human_scope_with_agent_only_legacy():
    cfg = resolve_config(
        cli={"scope": "agent,human"}, env={"BAND_API_KEY": "thnv_a_xyz"}
    )
    with pytest.raises(ConfigError):
        validate(cfg)


def test_validate_fails_agent_scope_with_human_only_legacy():
    cfg = resolve_config(cli={}, env={"BAND_API_KEY": "thnv_u_xyz"})
    with pytest.raises(ConfigError):
        validate(cfg)


def test_validate_fails_on_empty_scope():
    # Only unknown scope values → resolved scope is empty → validate fails.
    cfg = resolve_config(cli={"scope": "zzzzz"}, env={"BAND_API_KEY": "thnv_xyz"})
    # Defensive: empty scope should raise, since no scope means "serve nothing".
    with pytest.raises(ConfigError):
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
        "legacy_key",
        "warnings",
    }


def test_config_full_resolution_example():
    cfg = resolve_config(
        cli={
            "user_key": "thnv_u_cli",
            "agent_key": "thnv_a_cli",
            "room_id": "r_cli",
            "scope": "agent,human",
            "tools": "contacts,memory",
        },
        env={},
    )
    assert cfg.user_key == "thnv_u_cli"
    assert cfg.agent_key == "thnv_a_cli"
    assert cfg.room_id == "r_cli"
    assert cfg.scope == ["agent", "human"]
    assert cfg.tools == ["contacts", "memory"]
    assert cfg.legacy_key is None
    assert cfg.warnings == []
    validate(cfg)  # must not raise


# ---------------------------------------------------------------------------
# Warning message format (sanity)
# ---------------------------------------------------------------------------


def test_unknown_tools_warning_message_includes_suggestion():
    cfg = resolve_config(cli={"tools": "contact"}, env={})
    warn = next(w for w in cfg.warnings if w.kind == "unknown-tools-value")
    assert "did you mean 'contacts'" in warn.message
    assert "'contact'" in warn.message


def test_unknown_tools_warning_message_lists_valid_when_no_suggestion():
    cfg = resolve_config(cli={"tools": "zzz"}, env={})
    warn = next(w for w in cfg.warnings if w.kind == "unknown-tools-value")
    assert "contacts" in warn.message
    assert "memory" in warn.message


def test_legacy_ignored_warning_value_field():
    cfg = resolve_config(cli={}, env={"BAND_USER_KEY": "u", "BAND_API_KEY": "thnv_u_x"})
    warn = next(w for w in cfg.warnings if w.kind == "legacy-key-ignored")
    assert warn.value == "legacy_key"
    assert warn.did_you_mean is None
