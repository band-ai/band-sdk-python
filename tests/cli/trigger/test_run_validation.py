"""band-trigger refuses to start without an API key, a target handle and a message."""

from __future__ import annotations

import argparse

import pytest

from band.cli.trigger import DEFAULT_REST_URL, AuthMode, run


def _make_args(**overrides) -> argparse.Namespace:
    """Create a Namespace with sensible defaults for run()."""
    defaults = {
        "api_key": "test-api-key",
        "rest_url": DEFAULT_REST_URL,
        "auth_mode": AuthMode.AGENT,
        "target_handle": "@owner/agent",
        "message": "Hello agent",
        "timeout": 120,
        "verbose": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        args = _make_args(api_key=None)
        with pytest.raises(ValueError, match="API key is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_missing_target_handle_raises(self):
        args = _make_args(target_handle=None)
        with pytest.raises(ValueError, match="Target handle is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_missing_message_raises(self):
        args = _make_args(message=None)
        with pytest.raises(ValueError, match="Message is required"):
            await run(args)
