from __future__ import annotations

from band.adapters.opencode import OpencodeAdapter
from band.integrations.opencode.types import OpencodeSessionState
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_session_idle,
    make_platform_message,
    tools_protocol,
)


class TestSessionReuse:
    async def test_session_saved_before_registration_tracking_is_reused(
        self, tools
    ) -> None:
        """Upgrading must not discard sessions persisted without a registration name.

        ``mcp_server_name`` is only written to the session task event by adapters
        that record it, so every session saved before that carries None. Treating
        the unknown name as ours keeps an existing room's server-side conversation
        instead of silently starting over on the first turn after deploy.
        """
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_session_idle("sess-legacy")]]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(room_to_session={"room-1": "sess-legacy"}),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert fake_client.created_sessions == [], "abandoned a recoverable session"
        assert [call["session_id"] for call in fake_client.prompt_calls] == [
            "sess-legacy"
        ]
