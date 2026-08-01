"""BandSettings env_ignore_empty contract."""

from __future__ import annotations

import pytest

from band.core.bases import BandSettings


class _DemoSettings(BandSettings):
    demo_flag: bool = False
    demo_count: int = 7


def test_empty_env_var_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_FLAG", "")
    monkeypatch.setenv("DEMO_COUNT", "")
    settings = _DemoSettings()
    assert settings.demo_flag is False
    assert settings.demo_count == 7
