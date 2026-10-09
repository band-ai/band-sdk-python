"""The response wait: retries across empty poll windows up to a total budget.

A cold start (server warmup + the first NLP round-trips) can leave the first
poll window empty on a slow host. The wait must keep polling until the budget,
not abandon the turn after one empty window — otherwise a slow first turn is
silently dropped. This is the deterministic guard for that behavior: the live
E2E can only trigger it on a genuinely slow runner.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.adapters.parlant.responses import PARLANT_PREAMBLE_TAG, relay_agent_response
from tests.adapters.parlant.helpers import SENDER_NAME, SESSION_ID, agent_event

pytestmark = pytest.mark.usefixtures("parlant_sessions")

BOOKED = "Your table has been booked!"
PEANUTS = "Please note that our kitchen contains peanuts."
PREAMBLE = agent_event("One moment…", offset=1, tags=[PARLANT_PREAMBLE_TAG])
MULTIPART_BATCH = [
    PREAMBLE,
    agent_event(BOOKED, offset=2),
    agent_event(PEANUTS, offset=3),
]
ALREADY_ANSWERED = "Already answered."

# Large enough that a test finishing quickly proves the wait returned early.
LONG_BUDGET_SECONDS = 300.0
HANG_GUARD_SECONDS = 5


@pytest.fixture
def relay(mock_app, mock_tools):
    """Run the wait against ``mock_app`` within a hang guard."""

    async def run(*, timeout: float = 0.05, poll: float = 0.01) -> None:
        await asyncio.wait_for(
            relay_agent_response(
                app=mock_app,
                session_id=SESSION_ID,
                room_id=mock_tools.room_id,
                min_offset=0,
                tools=mock_tools,
                sender_name=SENDER_NAME,
                timeout=timeout,
                poll=poll,
            ),
            timeout=HANG_GUARD_SECONDS,
        )

    return run


def answer_after(mock_app, *, waits: list[bool], events: list) -> None:
    mock_app.sessions.wait_for_more_events = AsyncMock(side_effect=waits)
    mock_app.sessions.find_events = AsyncMock(return_value=events)


async def test_retries_past_empty_windows_then_forwards_late_reply(
    relay, mock_app, mock_tools
):
    """The reply only arrives on the 3rd wait, so forwarding it proves the loop
    retried past both empty windows instead of giving up on the first."""
    answer_after(
        mock_app,
        waits=[False, False, True],
        events=[agent_event("Hello there!", offset=2)],
    )

    await relay(timeout=LONG_BUDGET_SECONDS)

    mock_tools.assert_message_sent(
        content="Hello there!", mentions=[SENDER_NAME], count=1
    )


async def test_relays_all_final_parts_in_one_batch_without_preamble(
    relay, mock_app, mock_tools
):
    answer_after(
        mock_app,
        waits=[True],
        events=MULTIPART_BATCH,
    )

    await relay(timeout=LONG_BUDGET_SECONDS)

    mock_tools.assert_message_sent(
        content=f"{BOOKED}\n\n{PEANUTS}", mentions=[SENDER_NAME], count=1
    )


@pytest.mark.parametrize("declined", [False, True], ids=["tool-reply", "tool-decline"])
async def test_tool_reply_or_decline_suppresses_entire_final_batch(
    relay, mock_app, mock_tools, declined
):
    if declined:
        await mock_tools.no_reply("No response needed.")
    else:
        await mock_tools.send_message(ALREADY_ANSWERED, mentions=[SENDER_NAME])
    answer_after(
        mock_app,
        waits=[True],
        events=MULTIPART_BATCH,
    )

    await relay(timeout=LONG_BUDGET_SECONDS)

    if declined:
        mock_tools.assert_no_messages_sent()
    else:
        mock_tools.assert_message_sent(
            content=ALREADY_ANSWERED, mentions=[SENDER_NAME], count=1
        )


async def test_empty_window_after_a_tool_reply_ends_the_wait(
    relay, mock_app, mock_tools
):
    """Once a Band tool replied, an empty window means the engine is done:
    the wait returns at once instead of polling out the whole budget."""
    await mock_tools.send_message(ALREADY_ANSWERED, mentions=[SENDER_NAME])

    await relay(timeout=LONG_BUDGET_SECONDS)

    mock_app.sessions.wait_for_more_events.assert_awaited_once()


async def test_gives_up_after_budget_when_no_reply_ever_arrives(relay, mock_tools):
    """A genuinely silent turn is bounded: the wait returns once the budget
    elapses (the hang guard would raise otherwise) and nothing is forwarded."""
    await relay()

    mock_tools.assert_no_messages_sent()


async def test_preamble_only_times_out_without_forwarding_a_reply(
    relay, mock_app, mock_tools
):
    """Parlant emits a preamble then stalls the final generation. A preamble is an
    acknowledgment, not an answer, so it is never forwarded as the reply."""
    mock_app.sessions.wait_for_more_events = AsyncMock(
        side_effect=lambda **_: mock_app.sessions.find_events.await_count == 0
    )
    mock_app.sessions.find_events = AsyncMock(return_value=[PREAMBLE])

    await relay()

    mock_tools.assert_no_messages_sent()


async def test_empty_find_events_then_final_still_forwards(relay, mock_app, mock_tools):
    """A positive wait signal with an empty read is a transient visibility gap:
    the final is only query-visible on the 2nd read, so forwarding it proves
    the loop re-polled past the empty read instead of dropping the turn."""
    mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
    mock_app.sessions.find_events = AsyncMock(
        side_effect=[[], [agent_event("The answer.", offset=2)]]
    )

    await relay(timeout=LONG_BUDGET_SECONDS)

    mock_tools.assert_message_sent(
        content="The answer.", mentions=[SENDER_NAME], count=1
    )
