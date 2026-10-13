"""Codex adapter: aliased fields source from CODEX_* env names only, never bare vars."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig


class TestConfigEnvSourcing:
    """Aliased fields source from CODEX_* env names only, never bare vars."""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "EMIT_TURN_TASK_MARKERS",
            "CODEX_TURN_TASK_MARKERS",
            "CODEX_EMIT_TURN_TASK_MARKERS",
            "CODEX_COMMAND",
            "CODEX_CODEX_COMMAND",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_bare_env_var_never_populates_turn_task_markers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMIT_TURN_TASK_MARKERS", "true")

        assert CodexAdapterConfig().emit_turn_task_markers is False

    def test_legacy_env_name_populates_turn_task_markers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_TURN_TASK_MARKERS", "true")

        assert CodexAdapterConfig().emit_turn_task_markers is True

    def test_prefixed_field_name_env_populates_turn_task_markers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_EMIT_TURN_TASK_MARKERS", "true")

        assert CodexAdapterConfig().emit_turn_task_markers is True

    def test_codex_ws_url_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_WS_URL", "ws://elsewhere:9999")

        assert CodexAdapterConfig().codex_ws_url == "ws://elsewhere:9999"

    def test_codex_command_env_splits_shell_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CODEX_COMMAND (the established name), not the doubly-prefixed default."""
        monkeypatch.setenv("CODEX_COMMAND", "custom-codex --args")

        assert CodexAdapterConfig().codex_command == ("custom-codex", "--args")

    def test_codex_command_kwarg_wins_over_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_COMMAND", "ignored --value")

        config = CodexAdapterConfig(codex_command=("explicit", "--kwarg"))

        assert config.codex_command == ("explicit", "--kwarg")
