"""Reject runners without a pinned native Antigravity release."""

from __future__ import annotations

from types import ModuleType

import pytest


class TestReleaseSelection:
    @pytest.mark.parametrize(
        ("platform_id", "machine", "error"),
        [
            ("darwin", "x86_64", "unsupported Antigravity platform"),
            ("linux", "aarch64", "unsupported Antigravity architecture"),
            ("win32", "ARM64", "unsupported Antigravity architecture"),
        ],
    )
    def test_rejects_unpinned_runner(
        self, installer: ModuleType, platform_id: str, machine: str, error: str
    ) -> None:
        with pytest.raises(ValueError, match=error):
            installer.select_release(platform_id, machine)
