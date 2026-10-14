"""Session configuration resolves environment values and explicit overrides."""

from __future__ import annotations

import pytest

from band.runtime.sessionconfig import SessionConfig


class TestSessionSettings:
    def test_environment_values_are_typed_and_explicit_values_take_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENABLE_CONTEXT_HYDRATION", "false")
        monkeypatch.setenv("MAX_CONTEXT_MESSAGES", "25")
        monkeypatch.setenv("IDLE_RESYNC_SECONDS", "30")
        monkeypatch.setenv("MAX_CYCLE_SECONDS", "")

        config = SessionConfig(max_context_messages=50)

        assert config.model_dump(
            include={
                "enable_context_hydration",
                "max_context_messages",
                "idle_resync_seconds",
                "max_cycle_seconds",
            }
        ) == {
            "enable_context_hydration": False,
            "max_context_messages": 50,
            "idle_resync_seconds": 30,
            "max_cycle_seconds": None,
        }

    def test_environment_timing_is_checked_against_working_state_constraints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKING_KEEP_ALIVE_SECONDS", "5")

        with pytest.raises(ValueError, match="working_keep_alive_seconds"):
            SessionConfig(enable_working_state=True)

        config = SessionConfig(enable_working_state=False)
        assert config.working_keep_alive_seconds == 5
