"""Terminal writes remain best effort when request construction fails."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import Terminal


class TestSynchronousFailures:
    async def test_synchronous_request_failure_is_an_unconfirmed_boolean_false(
        self, terminal: Terminal, link: BandLink, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(**_: object) -> None:
            raise AttributeError("request construction failed")

        raw = link.rest.agent_api_messages.with_raw_response
        monkeypatch.setattr(raw, f"mark_agent_message_{terminal.action}", broken)

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED
        assert await terminal.legacy(link) is False
