"""Model strings start the upstream HTTPX2 provider and execute a real tool loop."""

from __future__ import annotations

import warnings

import httpx2
import pytest
from pydantic_ai.models import infer_model

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.pydanticai.providerpeer import ProviderPeer


class TestProvider:
    @pytest.mark.parametrize("alias", ["openai", "openai-chat"])
    async def test_default_provider_executes_a_tool_round_trip(
        self, monkeypatch: pytest.MonkeyPatch, alias: str
    ) -> None:
        peer = ProviderPeer()
        recorded: list[str] = []

        def record(note: str) -> str:
            """Record a note."""
            recorded.append(note)
            return "recorded"

        with peer.serve() as base_url, warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            monkeypatch.setenv("OPENAI_API_KEY", "test")
            monkeypatch.setenv("OPENAI_BASE_URL", base_url)
            adapter = PydanticAIAdapter(
                PydanticAIAdapterConfig(model=f"{alias}:configured-model"),
                additional_tools=[record],
            )
            await adapter.on_started("Bot", "A bot")
            assert adapter._agent is not None
            async with adapter._agent as agent:
                result = await agent.run("record hello", deps=FakeAgentTools())
            assert result.output == "done"
            model = infer_model(f"{alias}:configured-model")
            assert isinstance(model.provider.client._client, httpx2.AsyncClient)
            await model.provider.client.close()

        assert recorded == ["hello"]
        assert peer.tool_results() == ["recorded"]
        assert len(peer.requests) == 2
        assert all(request["model"] == "configured-model" for request in peer.requests)
        assert not [
            warning
            for warning in captured
            if "httpx.AsyncClient" in str(warning.message)
        ]
