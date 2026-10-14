"""A terminal-write outage warns once per intent, however often it is retried."""

from __future__ import annotations

import logging

import pytest

from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    MESSAGE_ID,
    Reply,
    SettlementPeer,
    Terminal,
    accepted,
)

LIFECYCLE_LOGGER = "band.platform.message_lifecycle"
OUTAGE = Reply(503)


@pytest.fixture(autouse=True)
def capture_lifecycle_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LIFECYCLE_LOGGER)


def warning_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LIFECYCLE_LOGGER and record.levelno >= logging.WARNING
    ]


class TestOutageLogging:
    async def test_the_first_unconfirmed_write_of_an_intent_warns(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE

        await terminal.detailed(link)

        lines = warning_lines(caplog)
        assert lines
        assert all(MESSAGE_ID in line for line in lines)

    async def test_repeated_unconfirmed_writes_of_the_same_intent_stay_quiet(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE
        await terminal.detailed(link)
        caplog.clear()

        await terminal.detailed(link)
        await terminal.detailed(link)

        assert warning_lines(caplog) == []

    async def test_an_outage_after_a_recovered_write_warns_again(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE
        await terminal.detailed(link)
        settlement_peer.reply = accepted(terminal.action)
        await terminal.detailed(link)
        caplog.clear()

        settlement_peer.reply = OUTAGE
        await terminal.detailed(link)

        assert warning_lines(caplog)
