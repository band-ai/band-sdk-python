from __future__ import annotations

from typing import Any

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import Emit
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    COMPUTE_BUDGET_S,
    FakeOpencodeClient,
    deliver_followup,
    event_message_updated_with_tokens,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)
from tests.adapters.usage_events import recorded_usage_payloads
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock


def _tokens(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "input": input_tokens,
        "output": output_tokens,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def _answered(message_id: str, *token_updates: dict[str, Any]) -> list[Any]:
    """One assistant message, updated with each of ``token_updates`` in order."""
    return [
        *(
            event_message_updated_with_tokens("sess-1", message_id, tokens)
            for tokens in token_updates
        ),
        event_text_part("sess-1", message_id, "done"),
    ]


class TestUsage:
    async def test_emits_turn_usage_folding_reasoning_into_output(
        self, make_adapter, tools
    ) -> None:
        """Emit.USAGE aggregates the assistant message's ``tokens``, folding
        OpenCode's disjoint ``reasoning`` count into ``output_tokens``."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated_with_tokens(
                        "sess-1",
                        "msg-1",
                        {
                            "input": 10,
                            "output": 5,
                            "reasoning": 3,
                            "cache": {"read": 1, "write": 2},
                        },
                    ),
                    event_text_part("sess-1", "msg-1", "done"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            client_factory=lambda _config: fake_client,
            emit=Emit.USAGE,
        )
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert recorded_usage_payloads(tools) == [
            {
                "input_tokens": 10,
                "output_tokens": 8,
                "cache_read_tokens": 1,
                "cache_write_tokens": 2,
            }
        ]

    async def test_sums_each_message_once_using_its_latest_update(
        self, make_adapter, tools
    ) -> None:
        """A tool step and the answer are two messages; OpenCode re-sends a
        message's running total, so a re-sent message must not double count."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    *_answered("msg-1", _tokens(10, 1), _tokens(10, 4)),
                    *_answered("msg-2", _tokens(20, 6)),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client, emit=Emit.USAGE)

        await run_single_turn(adapter, tools)

        assert recorded_usage_payloads(tools) == [_usage(30, 10)]

    async def test_each_turn_reports_only_its_own_usage(
        self, make_adapter, tools
    ) -> None:
        """A second turn in the same session reports its own tokens, not a
        running total that re-bills the first turn."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [*_answered("msg-1", _tokens(10, 2)), event_session_idle("sess-1")],
                [*_answered("msg-2", _tokens(7, 3)), event_session_idle("sess-1")],
            ]
        )
        adapter = make_adapter(fake_client, emit=Emit.USAGE)

        await run_single_turn(adapter, tools)
        await deliver_followup(adapter, tools, content="and then?")

        assert recorded_usage_payloads(tools) == [_usage(10, 2), _usage(7, 3)]

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_timed_out_turn_still_reports_the_tokens_it_spent(
        self, make_adapter, tools
    ) -> None:
        """Tokens spent before a timeout were billed, so the timeout path
        reports them like a completed turn would."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[_answered("msg-1", _tokens(12, 5))],
        )
        adapter = make_adapter(
            fake_client,
            config=OpencodeAdapterConfig(turn_timeout_s=COMPUTE_BUDGET_S),
            emit=Emit.USAGE,
        )

        async with (
            controlled_clock() as clock,
            background(run_single_turn(adapter, tools)) as turn,
        ):
            await adapter.watcher_started.wait()
            await clock.elapse(COMPUTE_BUDGET_S + 1)
            await turn

        assert fake_client.aborted_sessions == ["sess-1"]
        assert recorded_usage_payloads(tools) == [_usage(12, 5)]
