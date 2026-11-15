"""Offline Google client fixture for real ADK runner tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytest.importorskip("google.adk", reason="google-adk not installed")

from google.genai.models import AsyncModels

from tests.integrations.googleadk.kit import GoogleModelPeer


@pytest.fixture
def google_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[GoogleModelPeer]:
    peer = GoogleModelPeer()
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-adk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "offline-adk-test")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")

    async def generate(self: AsyncModels, **kwargs: Any) -> Any:
        return await peer.generate(**kwargs)

    monkeypatch.setattr(AsyncModels, "generate_content", generate)
    yield peer


@pytest.fixture
def closed_clients(monkeypatch: pytest.MonkeyPatch) -> list[httpx.AsyncClient]:
    """Observe actual external client closure without replacing cleanup."""
    clients: list[httpx.AsyncClient] = []
    real_close = httpx.AsyncClient.aclose

    async def close(client: httpx.AsyncClient) -> None:
        await real_close(client)
        clients.append(client)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", close)
    return clients
