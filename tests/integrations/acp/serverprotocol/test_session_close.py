"""Real ACP client/server exchanges for session lifecycle and failure outcomes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from acp import text_block

from band.integrations.acp.server_adapter import BandACPServerAdapter
from tests.integrations.acp.protocolkit import protocol_pair
from tests.integrations.acp.serverkit import (
    wait_for_pending_prompt,
)


class TestSessionClose:
    @pytest.mark.asyncio
    async def test_session_close_is_advertised_and_cleans_up_over_acp_wire(
        self, mock_rest_client: MagicMock, tmp_path: Path
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)

        async with protocol_pair(adapter) as (conn, _client):
            initialized = await conn.initialize(protocol_version=1)
            close = initialized.agent_capabilities.session_capabilities.close
            assert close is not None
            assert (
                close.model_dump(
                    mode="json", by_alias=True, exclude_none=True, exclude_unset=True
                )
                == {}
            )

            session = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
            listed = await conn.list_sessions()
            assert len(listed.sessions) == 1
            assert listed.sessions[0].session_id == session.session_id

            await conn.close_session(session_id=session.session_id)

            assert (await conn.list_sessions()).sessions == []

    @pytest.mark.asyncio
    async def test_session_close_cancels_active_prompt_over_acp_wire(
        self, mock_rest_client: MagicMock, tmp_path: Path
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)

        async with protocol_pair(adapter) as (conn, _client):
            initialized = await conn.initialize(protocol_version=1)
            assert initialized.agent_capabilities.session_capabilities.close is not None
            session = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
            room_id = adapter.get_room_for_session(session.session_id)
            assert room_id is not None

            async with asyncio.TaskGroup() as tasks:
                prompt = tasks.create_task(
                    conn.prompt(
                        session_id=session.session_id,
                        prompt=[text_block("Pending turn")],
                    )
                )
                await asyncio.wait_for(
                    wait_for_pending_prompt(adapter, room_id), timeout=1
                )

                await conn.close_session(session_id=session.session_id)

                assert (await prompt).stop_reason == "cancelled"

            assert (await conn.list_sessions()).sessions == []
