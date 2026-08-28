"""Tests for band.config.settings.RuntimeSettings."""

from __future__ import annotations

from band.config.settings import RuntimeSettings


def test_attachment_cache_maxsize_defaults_to_1000() -> None:
    assert RuntimeSettings().BAND_ATTACHMENT_CACHE_MAXSIZE == 1000


def test_attachment_cache_maxsize_overridable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("BAND_ATTACHMENT_CACHE_MAXSIZE", "42")

    assert RuntimeSettings().BAND_ATTACHMENT_CACHE_MAXSIZE == 42


def test_attachment_cache_maxsize_empty_env_falls_back_to_default(monkeypatch) -> None:
    """env_ignore_empty=True: a set-but-empty var must not raise a
    ValidationError trying to parse "" as an int."""
    monkeypatch.setenv("BAND_ATTACHMENT_CACHE_MAXSIZE", "")

    assert RuntimeSettings().BAND_ATTACHMENT_CACHE_MAXSIZE == 1000
