"""Integration test for the OpenCode manual-approval room round-trip.

This exercises what every unit test mocks out: the *real* platform send/receive
around ``RoomApprovals``. A manual permission relay posts an approval-request
message to a real room (the real REST API rejects a mention-less send, which the
fakes do not), and a real ``approve <id>`` reply comes back through the platform
carrying the leading ``@handle`` mention block the platform prepends -- the exact
shape ``strip_leading_mentions`` exists to handle. No LLM and no OpenCode server:
the OpenCode client is faked (its reply POST is not the seam under test), so this
stays an integration test.

Gated on ``BAND_API_KEY`` + ``BAND_API_KEY_USER``; not run in CI.

Run with:
    uv run pytest tests/integration/test_opencode_approval_roundtrip.py -v -s
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast
from uuid import uuid4

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.adapters.opencode.approvals import ApprovalPorts, RoomApprovals
from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import OpencodeClientProtocol, OpencodePermissionRequest
from band.runtime.formatters import replace_uuid_mentions
from band.runtime.tools import AgentTools
from tests.adapters.opencode.helpers import FakeOpencodeClient
from tests.integration.conftest import (
    requires_api,
    requires_user_api,
    send_user_mention,
)


async def _poll(fetch: Callable[[], object], *, timeout: float = 30.0) -> object:
    """Await ``fetch()`` until it returns a truthy value or the timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = await fetch()  # type: ignore[misc]
        if value:
            return value
        await asyncio.sleep(1.0)
    return None


# Read messages back through the USER client: it sees every room message
# (the agent's own outbound approval-request AND the user's reply) in the raw
# ``@[[uuid]]`` form. The agent client's list is its inbound queue and does not
# echo the agent's own posts.
async def _content_of(user_client, chat_id: str, message_id: str) -> str | None:
    resp = await user_client.human_api_messages.list_my_chat_messages(
        chat_id, page=1, page_size=100
    )
    return next(
        (m.content for m in (resp.data or []) if m.id == message_id and m.content),
        None,
    )


async def _content_containing(user_client, chat_id: str, needle: str) -> str | None:
    resp = await user_client.human_api_messages.list_my_chat_messages(
        chat_id, page=1, page_size=100
    )
    return next(
        (m.content for m in (resp.data or []) if needle in (m.content or "")),
        None,
    )


@requires_api
@requires_user_api
@pytest.mark.asyncio(loop_scope="session")
async def test_manual_approval_survives_a_real_platform_round_trip(
    shared_user_owned_room,
    shared_agent1_info,
    shared_user_peer,
    session_api_client,
    user_api_client,
):
    if (
        shared_user_owned_room is None
        or shared_agent1_info is None
        or shared_user_peer is None
    ):
        pytest.skip("user-owned room / agent / user peer unavailable")

    chat_id = shared_user_owned_room
    agent_id = shared_agent1_info.id
    # Mixed case so the round trip also guards request-id case preservation:
    # the server issues mixed-case ids and a reply must match them exactly.
    request_id = f"Req-{uuid4().hex[:8]}-Aa1"

    fake_client = FakeOpencodeClient()
    tools = AgentTools(chat_id, session_api_client, agent_id=agent_id)
    approvals = RoomApprovals(
        OpencodeAdapterConfig(approval_mode="manual"),
        ApprovalPorts(
            room_id=chat_id,
            session_id=lambda: "sess-1",
            client=lambda: cast(OpencodeClientProtocol, fake_client),
            tools=lambda: cast(AgentToolsProtocol, tools),
            # A real participant id: the platform validates the send's mentions.
            turn_mentions=lambda: [{"id": shared_user_peer.id}],
            release_turn_wait=lambda: None,
            is_own_band_tool=lambda _permission: False,
        ),
    )

    try:
        # Posts the approval-request message to the real room. A mention-less
        # send would 4xx here; reading it back proves the real API accepted it.
        await approvals.on_permission_asked(
            OpencodePermissionRequest(
                id=request_id, permission="bash", patterns=["rm -rf /tmp/x"]
            )
        )
        request_content = await _poll(
            lambda: _content_containing(user_api_client, chat_id, request_id)
        )
        assert request_content is not None, (
            "approval-request message never reached the room"
        )
        assert "approval requested" in request_content.lower()

        # A real user reply. The platform prepends the agent's @handle mention
        # block to the stored content -- the production shape on_message sees.
        reply_id = await send_user_mention(
            user_api_client, chat_id, agent_id, f"approve {request_id}"
        )
        raw_reply = await _poll(lambda: _content_of(user_api_client, chat_id, reply_id))
        assert raw_reply is not None, "user reply never landed in the room"

        # Mirror the inbound preprocessing (uuid mentions -> @handle) that runs
        # before on_message, then drive the reply exactly as the adapter would.
        participants = [{"id": agent_id, "handle": shared_agent1_info.handle}]
        delivered = replace_uuid_mentions(cast(str, raw_reply), participants)
        assert delivered.startswith("@"), "platform did not prepend a mention block"

        consumed = await approvals.try_handle_reply(delivered, shared_user_peer.id)
    finally:
        approvals.cancel()

    assert consumed, "the mentioned approve reply was not recognized"
    assert fake_client.permission_replies == [
        {"session_id": "sess-1", "permission_id": request_id, "response": "once"}
    ]
