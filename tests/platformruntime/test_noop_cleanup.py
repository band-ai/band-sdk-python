"""PlatformRuntime noop cleanup."""

from __future__ import annotations

import pytest

from band.runtime.platform_runtime import PlatformRuntime


class TestNoopCleanup:
    """Tests for the noop cleanup function."""

    @pytest.mark.asyncio
    async def test_noop_cleanup_does_nothing(self):
        """The noop cleanup should not raise."""
        await PlatformRuntime._noop_cleanup("room-123")
