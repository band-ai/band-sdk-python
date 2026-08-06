"""The console entry, on the platform the room view cannot run on."""

from __future__ import annotations

import sys

import pytest

from band.integrations.desktop_app.cli import entry_point


def test_windows_is_refused_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """fcntl and Unix sockets do not exist there, and the bare
    ``ModuleNotFoundError`` they would raise names neither the product nor
    the platform the user needs instead."""
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(SystemExit, match="macOS"):
        entry_point()
