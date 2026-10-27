"""Fresh receiver apps and external invocation doubles."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.loaders import load_script_module
from tests.paths import EXAMPLES_ROOT


@pytest.fixture
def platform_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure the receiver's environment and return its platform URL."""
    url = "https://receiver-platform.test"
    monkeypatch.setenv("BAND_AGENT_ID", "agent-1")
    monkeypatch.setenv("BAND_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-provider")
    monkeypatch.setenv("BAND_REST_URL", url)
    return url


@pytest.fixture
def receiver() -> ModuleType:
    return load_script_module(
        EXAMPLES_ROOT / "oneshot" / "server.py", "oneshot_receiver"
    )


@pytest.fixture
def invoker(receiver: ModuleType) -> MagicMock:
    invoker = MagicMock(handle_event=AsyncMock(return_value={"status": "done"}))
    receiver.app.state.invoker = invoker
    return invoker
