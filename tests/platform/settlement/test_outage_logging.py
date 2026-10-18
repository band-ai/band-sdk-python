"""Recent terminal-write intents suppress repeated outage warnings."""

from __future__ import annotations

import logging

import pytest

from band.platform.link import BandLink
from band.platform.message_lifecycle import UNCONFIRMED_WRITE_LIMIT
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


def outcome_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LIFECYCLE_LOGGER and " ended " in record.getMessage()
    ]


class TestOutageLogging:
    async def test_old_intents_expire_but_recent_retries_stay_quiet(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = Reply(400)
        oldest = Terminal(terminal.action, "oldest")
        await oldest.detailed(link)
        await terminal.detailed(link)
        for index in range(UNCONFIRMED_WRITE_LIMIT - 2):
            await Terminal(terminal.action, f"pending-{index}").detailed(link)
        await terminal.detailed(link)
        await Terminal(terminal.action, "newest").detailed(link)
        await Terminal(terminal.action, "next-newest").detailed(link)
        caplog.clear()

        await terminal.detailed(link)

        assert [record.levelno for record in outcome_records(caplog)] == [logging.DEBUG]

        await oldest.detailed(link)

        records = outcome_records(caplog)
        assert [record.levelno for record in records] == [
            logging.DEBUG,
            logging.WARNING,
        ]
        assert "oldest" in records[-1].getMessage()

    async def test_the_first_unconfirmed_write_of_an_intent_warns(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE

        await terminal.detailed(link)

        warnings = [
            record for record in caplog.records if record.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1
        assert MESSAGE_ID in warnings[0].getMessage()

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

        assert [record.levelno for record in outcome_records(caplog)] == [
            logging.DEBUG,
            logging.DEBUG,
        ]

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

        assert [record.levelno for record in outcome_records(caplog)] == [
            logging.WARNING
        ]

    async def test_a_changed_failure_cause_warns_again(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE
        await terminal.detailed(link)
        caplog.clear()

        settlement_peer.reply = Reply(401)
        await terminal.detailed(link)
        await terminal.detailed(link)

        assert [record.levelno for record in outcome_records(caplog)] == [
            logging.WARNING,
            logging.DEBUG,
        ]
