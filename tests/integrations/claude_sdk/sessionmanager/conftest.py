"""Fixtures for the ClaudeSessionManager tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.adapters.claude_sdk.fakecli import FakeClaude, scripted_claude


@pytest.fixture
def claude() -> Iterator[FakeClaude]:
    with scripted_claude() as claude:
        yield claude
