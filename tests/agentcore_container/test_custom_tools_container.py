"""Tests for examples/agentcore/custom_tools_llm_server.py.

Same shape as test_container.py (see that file for the boilerplate rationale).
This module additionally covers the custom-tool wiring that distinguishes
this variant: the ``WeatherInput``/``get_weather`` pair passed as
``additional_tools`` to the adapter.
"""

from __future__ import annotations

import asyncio
import os
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from band.runtime.oneshot import OneShotEnvelopeError
from tests.loaders import load_script_module
from tests.paths import EXAMPLES_ROOT

_CONTAINER_PATH = EXAMPLES_ROOT / "agentcore" / "custom_tools_llm_server.py"


def _load_container_module() -> ModuleType:
    # Provide env vars so the module imports cleanly (lifespan is lazy, not eager)
    os.environ.setdefault("BAND_AGENT_ID", "test-agent")
    os.environ.setdefault("BAND_API_KEY", "test-key")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic")
    return load_script_module(_CONTAINER_PATH, "custom_tools_llm_server")


container = _load_container_module()


class _FakeRequest:
    """Minimal stand-in for a Starlette Request — only .json() is used."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    async def json(self) -> dict[str, Any]:
        return self._body


def _set_invoker(handle_event: AsyncMock) -> MagicMock:
    """Install a mock OneShotInvoker on app.state and return it."""
    invoker = MagicMock()
    invoker.handle_event = handle_event
    container.app.state.invoker = invoker
    return invoker


# ---------------------------------------------------------------------------
# Custom tool wiring
# ---------------------------------------------------------------------------


class TestWeatherTool:
    async def test_get_weather_reports_city(self) -> None:
        args = container.WeatherInput(city="Lisbon")
        assert await container.get_weather(args) == "Lisbon: sunny, 22°C"

    def test_build_adapter_registers_weather_tool(self) -> None:
        adapter = container._build_adapter("test-anthropic")
        assert adapter._custom_tools == [
            (container.WeatherInput, container.get_weather)
        ]


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------


class TestRequireEnv:
    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("__TEST_VAR__", "value")
        assert container._require_env("__TEST_VAR__") == "value"

    def test_raises_on_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("__TEST_VAR__", raising=False)
        with pytest.raises(ValueError, match="__TEST_VAR__"):
            container._require_env("__TEST_VAR__")

    def test_raises_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("__TEST_VAR__", "   ")
        with pytest.raises(ValueError, match="__TEST_VAR__"):
            container._require_env("__TEST_VAR__")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestPingEndpoint:
    def test_returns_healthy(self) -> None:
        assert asyncio.run(container.ping()) == {"status": "Healthy"}


class TestInvocationsRoute:
    async def test_delegates_to_invoker(self) -> None:
        handle_event = AsyncMock(return_value={"status": "done", "message_id": "m1"})
        invoker = _set_invoker(handle_event)
        body = {"event_type": "message_created", "room_id": "r1", "payload": {}}

        result = await container.invocations(_FakeRequest(body))

        assert result == {"status": "done", "message_id": "m1"}
        handle_event.assert_awaited_once_with(body)
        # Route is a pure pass-through; it doesn't inspect the body itself.
        assert invoker.handle_event is handle_event

    async def test_envelope_error_maps_to_400(self) -> None:
        _set_invoker(AsyncMock(side_effect=OneShotEnvelopeError("missing room_id")))
        body = {"event_type": "message_created", "payload": {"id": "m1"}}

        with pytest.raises(HTTPException) as exc:
            await container.invocations(_FakeRequest(body))
        assert exc.value.status_code == 400
        assert "room_id" in exc.value.detail

    async def test_unexpected_error_maps_to_500(self) -> None:
        _set_invoker(AsyncMock(side_effect=RuntimeError("boom")))
        body = {"event_type": "message_created", "room_id": "r1", "payload": {}}

        with pytest.raises(HTTPException) as exc:
            await container.invocations(_FakeRequest(body))
        assert exc.value.status_code == 500
