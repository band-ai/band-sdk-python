"""Fresh receiver apps and external invocation doubles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import ModuleType
from unittest.mock import NonCallableMagicMock, create_autospec

import httpx
import pytest

from band.runtime.oneshot import OneShotInvoker
from tests.loaders import load_script_module
from tests.oneshot_server.kit import CREDENTIALS, PLATFORM_URL
from tests.paths import EXAMPLES_ROOT

# The shared receiver and its AgentCore custom-tools variant serve the same contract.
RECEIVERS = {
    "oneshot": EXAMPLES_ROOT / "oneshot" / "server.py",
    "custom-tools": EXAMPLES_ROOT / "agentcore" / "custom_tools_llm_server.py",
}


@pytest.fixture
def credentials(
    receiver: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Isolate the receiver's settings from the host, then set its credentials."""
    for field in receiver.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    for name, value in CREDENTIALS.items():
        monkeypatch.setenv(name, value)
    return CREDENTIALS


@pytest.fixture
def platform_url(credentials: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure the receiver's environment and return its platform URL."""
    monkeypatch.setenv("BAND_REST_URL", PLATFORM_URL)
    return PLATFORM_URL


@pytest.fixture(params=RECEIVERS)
def receiver(request: pytest.FixtureRequest) -> ModuleType:
    return load_script_module(RECEIVERS[request.param], f"receiver_{request.param}")


@pytest.fixture
async def client(receiver: ModuleType) -> AsyncIterator[httpx.AsyncClient]:
    """Call the receiver app in process; lifespan runs only when a test enters it."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=receiver.app), base_url="http://receiver"
    ) as client:
        yield client


@pytest.fixture
def invoker(receiver: ModuleType) -> NonCallableMagicMock:
    invoker = create_autospec(OneShotInvoker, instance=True)
    invoker.handle_event.return_value = {"status": "done"}
    receiver.app.state.invoker = invoker
    return invoker
