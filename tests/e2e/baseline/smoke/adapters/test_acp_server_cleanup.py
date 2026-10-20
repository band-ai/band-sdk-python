"""An embedding closes ACP ingress before releasing its host-owned transport."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from band.client.rest import AsyncRestClient, ParticipantRequest
from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.smoke.matrix.cleanup import BUDGET
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_agent_with_handle,
)


class TestACPServerCleanup:
    @with_adapters(Adapter.ANTHROPIC, prompt="Repeat the requested token exactly.")
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_embedding_closes_connections_before_its_rest_transport(
        self,
        agent: ProvisionedAgent,
        resource_manager: ResourceManager,
        tmp_path: Path,
    ) -> None:
        from acp import text_block  # noqa: PLC0415 -- ACP is absent from isolated framework lanes

        from band.integrations.acp.server_adapter import (  # noqa: PLC0415 -- imports the optional ACP protocol
            BandACPServerAdapter,
        )
        from tests.integrations.acp.protocolkit import (  # noqa: PLC0415 -- imports the optional ACP transport
            protocol_pair,
        )

        identity = await resource_manager.provision_agent("acp-cleanup")
        settings = resource_manager.settings
        async with httpx.AsyncClient() as transport:
            rest = AsyncRestClient(
                api_key=identity.api_key,
                base_url=settings.endpoints.rest_url,
                httpx_client=transport,
            )
            adapter = BandACPServerAdapter(rest_client=rest)
            adapter.add_cleanup(transport.aclose)
            with resource_manager.track_running(identity.id):
                async with running_agent_with_handle(
                    identity, adapter, settings
                ) as host:
                    async with protocol_pair(adapter) as (connection, client, _):
                        await connection.initialize(protocol_version=1)
                        session = await connection.new_session(
                            cwd=str(tmp_path), mcp_servers=[]
                        )
                        room_id = adapter.get_room_for_session(session.session_id)
                        assert room_id is not None
                        resource_manager.adopt_room(room_id)
                        await rest.agent_api_participants.add_agent_chat_participant(
                            room_id,
                            participant=ParticipantRequest(
                                participant_id=await resource_manager.user_ops.whoami(),
                                role="admin",
                            ),
                        )
                        await resource_manager.user_ops.add_participant(
                            room_id, agent.id
                        )
                        token = f"acp-cleanup-{identity.id}"
                        async with asyncio.timeout(BUDGET.deadline_s):
                            result = await connection.prompt(
                                session_id=session.session_id,
                                prompt=[text_block(f"Reply with exactly {token}")],
                            )
                        assert result.stop_reason == "end_turn"
                        assert any(
                            token
                            in getattr(
                                getattr(update.chunk, "content", None), "text", ""
                            )
                            for update in client.updates
                        )
                        assert not transport.is_closed
                    await host.stop()
                    assert transport.is_closed
