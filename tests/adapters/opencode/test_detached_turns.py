"""A turn released early to wait on a human is judged at its real end.

``on_event`` returns as soon as a permission ask parks the turn, so the
delivery is PROCESSED then; the turn's own outcome is judged when OpenCode
finishes it.
"""

from __future__ import annotations

from band.adapters.opencode import OpencodeAdapter
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.opencode.helpers import (
    FakeMCPBackend,
    FakeOpencodeClient,
    RawOpencodeEvent,
    ServerStep,
    agent_input,
    event_permission,
    event_session_idle,
    wait_for,
)

PERMISSION_ID = "req-1"


def parked_turn_client(*, after_approval: list[ServerStep]) -> FakeOpencodeClient:
    """OpenCode asks for a permission, then runs ``after_approval``."""
    parked: list[RawOpencodeEvent] = [event_permission("sess-1", PERMISSION_ID)]
    return FakeOpencodeClient(
        prompt_event_sequences=[parked],
        reply_permission_events={PERMISSION_ID: after_approval},
    )


async def park_turn(adapter: OpencodeAdapter, tools: FakeAgentTools) -> None:
    """Run a turn's ``on_event`` until the permission ask releases it."""
    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_event(agent_input("run it", tools))


async def approve(adapter: OpencodeAdapter) -> FakeAgentTools:
    approver_tools = FakeAgentTools()
    await adapter.on_event(agent_input(f"approve {PERMISSION_ID}", approver_tools))
    return approver_tools


async def test_a_detached_turn_that_ends_with_nothing_is_reported_once(
    make_adapter, tools
) -> None:
    adapter = make_adapter(
        parked_turn_client(after_approval=[event_session_idle("sess-1")])
    )

    await park_turn(adapter, tools)
    assert failure_reports(tools) == []

    await approve(adapter)
    await wait_for(lambda: bool(failure_reports(tools)))
    await adapter.on_cleanup("room-1")

    assert failure_reports(tools) == [MISSING_REPLY_FAILURE]


async def test_a_busy_message_during_a_detached_turn_is_settled(
    make_adapter, tools, mcp_backend: FakeMCPBackend
) -> None:
    """The busy message's own turn is settled, and the detached turn's band
    tool calls still resolve to the detached turn's tools."""
    adapter = make_adapter(
        parked_turn_client(
            after_approval=[
                mcp_backend.band_tool_call(
                    BandTool.SEND_MESSAGE, {"content": "Done.", "mentions": ["@alice"]}
                ),
                event_session_idle("sess-1"),
            ]
        )
    )
    busy_tools = FakeAgentTools()

    await park_turn(adapter, tools)
    await adapter.on_event(agent_input("are you done?", busy_tools))
    await approve(adapter)
    await wait_for(lambda: tools.turn.replied)
    await adapter.on_cleanup("room-1")

    assert busy_tools.turn.complete
    assert busy_tools.messages_sent == []
    assert tools.messages_sent[-1]["content"] == "Done."
    assert failure_reports(tools) == []


async def test_a_detached_turn_cancelled_by_cleanup_posts_nothing(
    make_adapter, tools
) -> None:
    adapter = make_adapter(parked_turn_client(after_approval=[]))

    await park_turn(adapter, tools)
    await adapter.on_cleanup("room-1")

    assert failure_reports(tools) == []
