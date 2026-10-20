"""Recent terminal-write intents suppress repeated outage warnings."""

from __future__ import annotations

import logging

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.link import BandLink
from band.platform.message_lifecycle import UNCONFIRMED_WRITE_LIMIT, logger
from tests.platform.settlement.kit import (
    MESSAGE_ID,
    MISSING_ATTEMPT,
    NOT_FOUND,
    Reply,
    SettlementPeer,
    Terminal,
    accepted,
    link_over,
    outcome_levels,
    refused_rest_client,
)

OUTAGE = Reply(503)


@pytest.fixture(autouse=True)
def capture_lifecycle_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=logger.name)


async def record_distinct_intents(
    terminal: Terminal, link: BandLink, count: int
) -> list[Terminal]:
    intents = [Terminal(terminal.action, f"intent-{index}") for index in range(count)]
    for intent in intents:
        await intent.detailed(link)
    return intents


class TestOutageLogging:
    @pytest.mark.parametrize(
        ("extra_intents", "expected"),
        [
            pytest.param(0, logging.DEBUG, id="at-the-limit"),
            pytest.param(1, logging.WARNING, id="over-the-limit"),
        ],
    )
    async def test_the_oldest_intent_is_forgotten_only_past_the_limit(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
        extra_intents: int,
        expected: int,
    ) -> None:
        settlement_peer.reply = OUTAGE
        oldest, *_ = await record_distinct_intents(
            terminal, link, UNCONFIRMED_WRITE_LIMIT + extra_intents
        )
        caplog.clear()

        await oldest.detailed(link)

        assert outcome_levels(caplog) == [expected]

    async def test_a_recent_retry_keeps_its_intent_out_of_eviction(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE
        retried, evicted, *_ = await record_distinct_intents(
            terminal, link, UNCONFIRMED_WRITE_LIMIT
        )
        await retried.detailed(link)
        await Terminal(terminal.action, "overflow").detailed(link)
        caplog.clear()

        await retried.detailed(link)
        await evicted.detailed(link)

        assert outcome_levels(caplog) == [logging.DEBUG, logging.WARNING]

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

        assert outcome_levels(caplog) == [logging.DEBUG, logging.DEBUG]

    async def test_repeated_transport_failures_of_the_same_intent_stay_quiet(
        self, terminal: Terminal, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with refused_rest_client() as rest:
            link = link_over(rest)
            await terminal.detailed(link)
            await terminal.detailed(link)

        assert outcome_levels(caplog) == [logging.WARNING, logging.DEBUG]

    async def test_the_two_terminal_actions_of_a_message_are_separate_intents(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settlement_peer.reply = OUTAGE
        other_action = (
            DeliveryStatus.FAILED
            if terminal.action is DeliveryStatus.PROCESSED
            else DeliveryStatus.PROCESSED
        )

        await terminal.detailed(link)
        await Terminal(other_action).detailed(link)

        assert outcome_levels(caplog) == [logging.WARNING, logging.WARNING]

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

        assert outcome_levels(caplog) == [logging.WARNING]

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

        assert outcome_levels(caplog) == [logging.WARNING, logging.DEBUG]

    @pytest.mark.parametrize(
        "definitive", [NOT_FOUND, MISSING_ATTEMPT], ids=["not-found", "rejected"]
    )
    async def test_a_definitive_result_always_warns_and_forgets_the_outage(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        caplog: pytest.LogCaptureFixture,
        definitive: Reply,
    ) -> None:
        settlement_peer.reply = OUTAGE
        await terminal.detailed(link)
        caplog.clear()

        settlement_peer.reply = definitive
        await terminal.detailed(link)
        await terminal.detailed(link)
        settlement_peer.reply = OUTAGE
        await terminal.detailed(link)

        assert outcome_levels(caplog) == [logging.WARNING] * 3
