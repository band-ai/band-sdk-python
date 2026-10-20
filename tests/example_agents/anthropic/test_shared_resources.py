"""The example's actual async tool handlers share a real HTTP connection pool."""

from __future__ import annotations

import json

import pytest

from tests.adaptercleanup import HostKind, host_for
from tests.example_agents.anthropic.kit import EXAMPLE
from tests.example_agents.anthropic.peer import shared_api


class TestSharedResources:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_both_actual_handlers_use_resource_until_host_disposes_it(
        self, kind: HostKind
    ) -> None:
        async with shared_api() as (client, base_url, values):
            adapter = EXAMPLE["build_adapter"](
                client,
                base_url,
                config=EXAMPLE["AnthropicAdapterConfig"](provider_key="test-key"),
            )
            todo, user = adapter._custom_tools
            async with host_for(adapter, kind) as host:
                await host.start()
                assert json.loads(await todo[1](todo[0](todo_id=1))) == values["todo"]
                assert json.loads(await user[1](user[0](user_id=1))) == values["user"]
                await host.stop()
                assert client.is_closed
                with pytest.raises(RuntimeError, match="closed"):
                    await todo[1](todo[0](todo_id=1))
