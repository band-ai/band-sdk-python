"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from .helpers import _make_fake_mcp_backend_factory


@pytest.fixture(autouse=True)
def patch_mcp_backend() -> Any:
    """Patch MCP backend creation for every OpenCode adapter test."""
    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        _make_fake_mcp_backend_factory(),
    ):
        yield
