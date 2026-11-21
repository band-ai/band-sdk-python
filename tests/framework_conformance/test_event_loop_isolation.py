"""Synchronous tests do not implicitly acquire an asyncio loop."""

from __future__ import annotations

import asyncio

import pytest


class TestEventLoopIsolation:
    def test_synchronous_tests_have_no_default_loop(self) -> None:
        with pytest.raises(RuntimeError):
            asyncio.get_event_loop()
