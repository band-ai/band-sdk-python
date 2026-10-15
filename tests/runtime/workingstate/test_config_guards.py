from __future__ import annotations

import pytest

from band.runtime.sessionconfig import SessionConfig


class TestSessionConfigGuards:
    def test_defaults_are_valid(self) -> None:
        cfg = SessionConfig()
        assert cfg.enable_working_state is True
        assert cfg.working_keep_alive_seconds == 3.0
        assert cfg.working_request_timeout_seconds == 2
        assert cfg.max_working_state_seconds is None

    def test_cadence_must_be_below_ttl_half(self) -> None:
        with pytest.raises(ValueError):
            SessionConfig(working_keep_alive_seconds=5.0)  # >= 10/2

    def test_cadence_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SessionConfig(working_keep_alive_seconds=0)

    def test_timeout_must_be_below_cadence(self) -> None:
        with pytest.raises(ValueError):
            SessionConfig(
                working_keep_alive_seconds=3.0, working_request_timeout_seconds=3
            )

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SessionConfig(working_request_timeout_seconds=0)

    def test_max_duration_if_set_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SessionConfig(max_working_state_seconds=0)

    def test_max_cycle_seconds_if_set_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_cycle_seconds"):
            SessionConfig(max_cycle_seconds=0)
        with pytest.raises(ValueError, match="max_cycle_seconds"):
            SessionConfig(max_cycle_seconds=-1.0)

    def test_disabled_skips_guards(self) -> None:
        # When disabled, odd values must not raise.
        cfg = SessionConfig(
            enable_working_state=False, working_keep_alive_seconds=100.0
        )
        assert cfg.enable_working_state is False

    def test_max_cycle_seconds_guard_is_independent_of_working_state(self) -> None:
        # max_cycle_seconds is a hang-killer unrelated to working-state
        # reporting, so its guard must still fire even when working-state
        # reporting itself is disabled.
        with pytest.raises(ValueError, match="max_cycle_seconds"):
            SessionConfig(enable_working_state=False, max_cycle_seconds=0)
