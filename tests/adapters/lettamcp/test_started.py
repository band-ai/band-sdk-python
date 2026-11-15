"""Letta MCP registration during adapter startup."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
)
from tests.adapters.lettakit import (
    make_mock_mcp_server,
    make_mock_mcp_tool,
    mock_org_user_provisioned,
)
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by


class TestLettaAdapterOnStarted:
    @pytest.mark.asyncio
    async def test_on_started_registers_external_mcp(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    mode="external", server_url="http://localhost:8002/sse"
                )
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_tools = [
            make_mock_mcp_tool("t1", "band_send_message"),
            make_mock_mcp_tool("t2", "band_send_event"),
        ]
        mock_client.mcp_servers.tools.list.return_value = mock_tools

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="https://api.letta.com",
        )
        mock_client.mcp_servers.create.assert_called_once_with(
            server_name="band",
            config={
                "mcp_server_type": "sse",
                "server_url": "http://localhost:8002/sse",
            },
        )
        assert adapter._mcp.server_id == mock_server.id
        assert adapter._mcp.tool_ids == ["t1", "t2"]
        # External mode starts no local server.
        assert adapter._mcp.backend.current is None
        assert adapter._system_prompt  # non-empty

    @pytest.mark.asyncio
    async def test_on_started_self_host_registers_advertised_url(self) -> None:
        """Self-host mode starts the local server, binds the configured host,
        and registers its advertised URL under an instance-unique name."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    bind_host="0.0.0.0", advertised_host="host.docker.internal"
                )
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t1", "band_send_message"),
        ]

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        fake_backend = FakeBandMCPBackend()

        with (
            patch.dict("sys.modules", {"letta_client": mock_letta_module}),
            backends_created_by(fake_backend) as starts,
        ):
            await adapter.on_started("TestBot", "A test bot")

        assert starts.requested[0].host == "0.0.0.0"
        create_kwargs = mock_client.mcp_servers.create.call_args.kwargs
        # Fresh unique name per registration: Letta soft-deletes registrations,
        # so a name can never be reused once deregistered.
        assert re.fullmatch(r"band-[0-9a-f]{8}", create_kwargs["server_name"])
        assert create_kwargs["config"] == {
            "mcp_server_type": "sse",
            "server_url": f"http://host.docker.internal:{fake_backend.local_server.port}/sse",
            "custom_headers": fake_backend.headers,
        }
        assert adapter._mcp.backend.current is fake_backend
        assert adapter._mcp.server_id == mock_server.id

    @pytest.mark.asyncio
    async def test_on_started_forwards_cloud_params(self) -> None:
        """provider_key and project are forwarded to AsyncLetta when configured."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url="https://api.letta.com",
                provider_key="letta-key-123",
                project="my-project",
                mcp=LettaMCPConfig(mode="external"),
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = []

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="https://api.letta.com",
            api_key="letta-key-123",
            project="my-project",
        )

    @pytest.mark.asyncio
    async def test_on_started_mcp_registration_failure_raises(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )

        mock_client = AsyncMock()
        mock_client.mcp_servers.create.side_effect = ConnectionError("refused")

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with (
            patch.dict("sys.modules", {"letta_client": mock_letta_module}),
            pytest.raises(RuntimeError, match="MCP server registration failed"),
        ):
            await adapter.on_started("TestBot", "A test bot")

    @pytest.mark.asyncio
    async def test_on_started_self_hosted_org_scoped_by_default(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """A self-hosted base_url auto-enables org scoping: a dedicated
        org+user is provisioned and its user_id becomes the client's default
        header — this is what isolates MCP tool storage between instances."""
        base_url = "http://localhost:8283"
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url=base_url, mcp=LettaMCPConfig(mode="external")
            )
        )

        mock_org_user_provisioned(
            httpx_mock,
            base_url=base_url,
            org_id="org-1",
            user_id="user-1",
            name="band-TestBot",
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = []

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url=base_url,
            default_headers={"user_id": "user-1"},
        )
        # Base tools must be force-seeded for the fresh org before any room
        # can reach agents.create(include_base_tools=True).
        mock_client.tools.list.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.letta.com",
            "https://API.LETTA.COM/",
            "  https://api.letta.com  ",
        ],
    )
    async def test_on_started_cloud_stays_unscoped(self, base_url: str) -> None:
        """Regression guard: a Cloud base_url must never invoke org-scope
        resolution — the Cloud path stays byte-for-byte unchanged."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url=base_url, mcp=LettaMCPConfig(mode="external")
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = []

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with (
            patch.dict("sys.modules", {"letta_client": mock_letta_module}),
            patch("band.adapters.letta.resolve_org_scoped_headers") as mock_resolve,
        ):
            await adapter.on_started("TestBot", "A test bot")

        mock_resolve.assert_not_called()
        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url=base_url,
        )
        mock_client.tools.list.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_started_org_scoped_false_opts_out_on_self_hosted(
        self,
    ) -> None:
        """org_scoped=False is the escape hatch: it opts a self-hosted
        deployment back out of org scoping."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url="http://localhost:8283",
                org_scoped=False,
                mcp=LettaMCPConfig(mode="external"),
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = []

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with (
            patch.dict("sys.modules", {"letta_client": mock_letta_module}),
            patch("band.adapters.letta.resolve_org_scoped_headers") as mock_resolve,
        ):
            await adapter.on_started("TestBot", "A test bot")

        mock_resolve.assert_not_called()
        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="http://localhost:8283",
        )

    @pytest.mark.asyncio
    async def test_on_started_import_error(self) -> None:
        adapter = LettaAdapter()

        with (
            patch.dict("sys.modules", {"letta_client": None}),
            pytest.raises(ImportError, match="letta-client is required"),
        ):
            await adapter.on_started("TestBot", "A test bot")
