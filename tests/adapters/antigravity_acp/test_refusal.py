"""Report a native refusal once without manufacturing a resumable seed."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from band.converters.acp_client import ACP_CLIENT_RESUME_RECORD
from band.integrations.acp.types import ACP_FAILURE_PROVIDER, REFUSAL_STOP
from band.testing.fake_tools import failure_reports
from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerSettings
from tests.integrations.acp.clientbehavior.kit import QUOTA_TEXT


class TestAntigravityRefusal:
    async def test_a_first_refusal_is_visible_and_not_recorded(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(
            PeerSettings(reply=None, text=QUOTA_TEXT, stop_reason=REFUSAL_STOP), emit=()
        )

        tools = await peer.send()

        assert failure_reports(tools) == [(ACP_FAILURE_PROVIDER, QUOTA_TEXT)]
        assert ACP_CLIENT_RESUME_RECORD.candidates(tools.events_sent) == {}
