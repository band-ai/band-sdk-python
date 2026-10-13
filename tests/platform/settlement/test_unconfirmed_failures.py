"""Failures that prove nothing about the message stay UNCONFIRMED."""

from __future__ import annotations

import pytest
from band_rest.core import ParsingError

from band.platform.link import BandLink
from band.platform.message_lifecycle import SettlementResult
from tests.platform.settlement.kit import (
    Reply,
    SettlementPeer,
    Terminal,
    error_body,
    link_over,
    refused_rest_client,
)


class TestUnconfirmedFailures:
    @pytest.mark.parametrize("status", [401, 403, 409, 503])
    async def test_an_error_status_is_unconfirmed(
        self, terminal: Terminal, peer: SettlementPeer, link: BandLink, status: int
    ) -> None:
        peer.reply = Reply.of_json(status, error_body("refused"))

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED

    async def test_a_retryable_status_is_retried_inside_one_logical_write(
        self, terminal: Terminal, peer: SettlementPeer, link: BandLink
    ) -> None:
        peer.reply = Reply(503)

        await terminal.detailed(link)

        assert peer.writes == [terminal.write] * 4

    async def test_a_transport_failure_is_unconfirmed(self, terminal: Terminal) -> None:
        async with refused_rest_client() as rest:
            result = await terminal.detailed(link_over(rest))

        assert result is SettlementResult.UNCONFIRMED

    async def test_a_parsing_error_without_a_status_is_unconfirmed(
        self, terminal: Terminal, link: BandLink, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The generated client always sets a status, so this boundary cannot be
        # reached over the wire.
        async def unparsable(**_: object) -> None:
            raise ParsingError(body={"error": "not found"})

        raw = link.rest.agent_api_messages.with_raw_response
        monkeypatch.setattr(raw, f"mark_agent_message_{terminal.action}", unparsable)

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED
