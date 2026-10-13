"""Fixtures for the ClaudeSessionManager tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from tests.adapters.claude_sdk.fakecli import FakeClaude


@pytest.fixture
def claude() -> Iterator[FakeClaude]:
    claude = FakeClaude()
    with patch(
        "band.integrations.claude_sdk.session_manager.create_transport",
        claude.transport,
    ):
        yield claude
    claude.assert_done()
