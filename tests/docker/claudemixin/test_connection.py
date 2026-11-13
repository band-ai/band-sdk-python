from __future__ import annotations

from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock
from pytest_mock import MockerFixture


class TestConnection:
    @pytest.mark.asyncio
    async def test_receiver_uses_the_workload_cli_and_working_directory(
        self,
        bridge: ModuleType,
        options: SimpleNamespace,
        httpx_mock: HTTPXMock,
        mocker: MockerFixture,
    ) -> None:
        httpx_mock.add_response(
            json={"data": {"id": "test-agent", "handle": "owner/agent"}}
        )
        agent = object()
        create = mocker.patch.object(bridge.Agent, "create", return_value=agent)
        run = mocker.patch.object(
            bridge, "run_with_graceful_shutdown", new_callable=AsyncMock
        )
        await bridge.main(options)
        adapter = create.call_args.kwargs["adapter"]
        assert adapter.config.cli.cli_path == str(options.workspace / "claude")
        assert adapter.config.cwd == options.workspace
        assert adapter.config.setting_sources == ("user",)
        run.assert_awaited_once_with(agent)
