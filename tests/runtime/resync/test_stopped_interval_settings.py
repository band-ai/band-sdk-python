"""Stopped-room settings resolve and validate at session construction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.runtime.types import SessionConfig


class TestStoppedIntervalSettings:
    @pytest.mark.parametrize(
        ("environment", "overrides", "expected"),
        [
            ("300", {}, 300),
            ("", {}, None),
            ("300", {"stopped_resync_seconds": 15}, 15),
            ("300", {"stopped_resync_seconds": None}, None),
            ("invalid", {"stopped_resync_seconds": 15}, 15),
        ],
        ids=["environment", "empty", "explicit", "base", "overridden-invalid-env"],
    )
    def test_explicit_interval_overrides_environment_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        environment: str,
        overrides: dict[str, float | None],
        expected: float | None,
    ) -> None:
        monkeypatch.setenv("STOPPED_RESYNC_SECONDS", environment)
        config = SessionConfig(**overrides)
        assert config.stopped_resync_seconds == expected

    @pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf", "-inf"])
    def test_invalid_environment_interval_fails_before_execution(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("STOPPED_RESYNC_SECONDS", value)
        with pytest.raises(ValidationError, match="stopped_resync_seconds"):
            SessionConfig()
