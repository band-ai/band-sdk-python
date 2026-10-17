"""The example keeps resource cleanup with the owner across startup failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.agent import Agent
from tests.core.agentlifecycle.kit import make_runtime
from tests.example_agents.anthropic.kit import EXAMPLE
from tests.example_agents.anthropic.peer import shared_api


class TestFailureRecovery:
    async def test_construction_failure_releases_application_owned_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "agent_config.yaml"
        config.write_text(
            "anthropic_agent:\n  agent_id: test-agent\n  api_key: test-key\n"
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        original = ValueError("Host construction failed")

        def refuse(*args: object, **kwargs: object) -> Agent:
            raise original

        async with shared_api() as (client, _url, _values):
            monkeypatch.setattr(EXAMPLE["httpx"], "AsyncClient", lambda: client)
            monkeypatch.setattr(Agent, "from_config", refuse)
            settings = EXAMPLE["Settings"](SHARED_RESOURCES_CONFIG=config)
            with pytest.raises(ValueError) as raised:
                await EXAMPLE["build_agent"](settings)
            assert raised.value is original
            assert client.is_closed

    @pytest.mark.parametrize("failed_closes", [1, 2])
    async def test_start_failure_preserves_error_and_retries_original_host(
        self, monkeypatch: pytest.MonkeyPatch, failed_closes: int
    ) -> None:
        runtime = make_runtime()
        original = LookupError("Metadata failed")
        runtime.initialize.side_effect = original
        async with shared_api() as (client, url, _values):
            close = client.aclose
            attempts = 0

            async def flaky_close() -> None:
                nonlocal attempts
                attempts += 1
                if attempts <= failed_closes:
                    raise OSError("Client cleanup failed")
                await close()

            monkeypatch.setattr(client, "aclose", flaky_close)
            adapter = EXAMPLE["build_adapter"](
                client,
                url,
                config=EXAMPLE["AnthropicAdapterConfig"](provider_key="test-key"),
            )
            agent = Agent(runtime=runtime, adapter=adapter)
            with pytest.raises(LookupError) as raised:
                await EXAMPLE["serve"](agent)
            assert raised.value is original
            assert attempts == 2
            if failed_closes == 2:
                assert any("retain this Agent" in note for note in original.__notes__)
                assert not client.is_closed
                await agent.stop()
            assert client.is_closed
