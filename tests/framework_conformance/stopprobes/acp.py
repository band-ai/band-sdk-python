"""Backend-stop probes for the ACP client adapter; see ``stopprobes``.

The adapter is paired in process with a scripted ACP agent over real JSON-RPC,
and the agent records each ``session/cancel`` and connection exit it sees.
CopilotACPAdapter keeps this adapter's ``on_message`` and ``backend_turns``,
so these cases cover it too.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from band.client.streaming import ControlMode
from band.core.protocols import TurnResultAlreadyReported
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_room,
    wait_until,
    waiting_on_stop,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

if TYPE_CHECKING:
    from band.integrations.acp.client_adapter import ACPClientAdapter
    from tests.integrations.acp.acp_toolkit import FakeACPAgent

# The scripted agent's first session, as it records the cancel and the exit.
CANCEL_FIRST_SESSION = "session/cancel fake-session-1"
CONNECTION_EXIT = "connection exit"
# Short enough that the timeout case ends at once.
PROBE_TURN_TIMEOUT_S = 0.05
# A thought from the turn being cancelled. It must not flush on the next turn.
OLD_THOUGHT = "old-turn-thought"
REBOUND_BEFORE_STOP = "rebound before the stop settled"


def prompting_agent() -> FakeACPAgent:
    """An agent whose prompt keeps running until its connection closes."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        FakeACPAgent,
    )

    agent = FakeACPAgent()

    @agent.on_prompt
    async def keep_prompting(fake: FakeACPAgent, session_id: str) -> None:
        await asyncio.Future[None]()

    return agent


def cancels(agent: FakeACPAgent) -> list[str]:
    return [record for record in agent.lifecycle if record != CONNECTION_EXIT]


def acp_adapter(**settings: object) -> ACPClientAdapter:
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- client_adapter imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        ACPClientAdapter,
    )
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        fake_agent_config,
    )

    return ACPClientAdapter(fake_agent_config(**settings))


async def end_prompting_turn(adapter: ACPClientAdapter, mode: ControlMode) -> list[str]:
    """End a turn whose prompt is in flight; the agent's cancels."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        await wait_until(lambda: bool(agent.prompts))
        await end_turn(adapter, turn, ROOM_ID, mode)
        return cancels(agent)


async def idle() -> list[str]:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = acp_adapter()
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        await stop_room(adapter, ROOM_ID)
        return cancels(agent)


async def timed_out() -> list[str]:
    """The cancel goes out before the timed-out room's runtime is stopped."""
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- acp extra
        ACPTurnTimeoutError,
    )
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = acp_adapter(turn_timeout_s=PROBE_TURN_TIMEOUT_S)
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        with suppress(ACPTurnTimeoutError):
            await adapter.on_event(turn_input(FakeAgentTools()))
        await wait_until(lambda: CONNECTION_EXIT in agent.lifecycle)
        return list(agent.lifecycle)


def _on_message(task: asyncio.Task[None]) -> object | None:
    """The suspended ``on_message`` coroutine, if this task is inside it."""
    coro = task.get_coro()
    while coro is not None:
        code = getattr(coro, "cr_code", None)
        if code is not None and code.co_name == "on_message":
            return coro
        coro = getattr(coro, "cr_await", None)
    return None


def rebound_before_stop(task: asyncio.Task[None]) -> bool:
    """The next ``on_message`` acquired its runtime before the previous stop settled."""
    frame = getattr(_on_message(task), "cr_frame", None)
    return frame is not None and "runtime" in frame.f_locals


def held_for_the_previous_stop(task: asyncio.Task[None]) -> bool:
    """The next turn is blocked on the previous stop, before or inside ``running``."""
    if waiting_on_stop(task):
        return True
    message = _on_message(task)
    if getattr(message, "cr_frame", None) is None:
        return False
    awaited = getattr(message, "cr_await", None)
    code = getattr(awaited, "cr_code", None)
    # ``running()`` waits for settlement inside its ``__aenter__``, after reset.
    return (
        code is not None and code.co_name == "__aenter__" and rebound_before_stop(task)
    )


def thoughts(tools: FakeAgentTools) -> list[str]:
    return [
        str(event["content"])
        for event in tools.events_sent
        if event["message_type"] == "thought"
    ]


async def next_turn_waits_for_the_previous_stop() -> list[str]:
    """A thought emitted while the previous cancel is held stays off the next turn.

    ``session/cancel`` is a notification, so the agent's handler returns after
    the client has already finished sending it. The owned stop is what
    ``settled`` waits on, so the probe holds that call after the real
    notification has been sent.
    """
    from acp.helpers import (  # noqa: PLC0415 -- acp extra
        update_agent_thought_text,
    )

    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- acp extra
        started_acp_adapter,
    )

    agent = prompting_agent()

    async def first_prompt_hangs(_fake: FakeACPAgent, session_id: str) -> None:
        del session_id
        if len(agent.prompts) == 1:
            await asyncio.Future[None]()

    agent.on_prompt(first_prompt_hangs)
    adapter = acp_adapter()
    sent = asyncio.Event()
    release = asyncio.Event()
    async with started_acp_adapter(adapter, agent):
        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        await wait_until(lambda: bool(agent.prompts))
        session_id = agent.session_ids()[0]
        runtime = adapter._rooms[ROOM_ID].runtime
        assert runtime is not None
        cancel_turn = runtime.cancel_turn

        async def hold_cancel(held_session_id: str) -> None:
            await cancel_turn(held_session_id)
            sent.set()
            await release.wait()

        runtime.cancel_turn = hold_cancel
        turn.cancel()
        with suppress(asyncio.CancelledError):
            await turn
        await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await sent.wait()
        nxt_tools = FakeAgentTools()
        nxt = asyncio.create_task(adapter.on_event(turn_input(nxt_tools)))
        try:
            await wait_until(lambda: held_for_the_previous_stop(nxt))
            rebound = rebound_before_stop(nxt)
            await agent.emit(session_id, update_agent_thought_text(OLD_THOUGHT))
            release.set()
            with suppress(TurnResultAlreadyReported):
                await nxt
        finally:
            release.set()
            if not nxt.done():
                nxt.cancel()
                with suppress(asyncio.CancelledError):
                    await nxt
        return [
            *([REBOUND_BEFORE_STOP] if rebound else []),
            *thoughts(nxt_tools),
        ]


async def cleanup_after_stop() -> list[str]:
    """Leaving the room right after a STOP stops the runtime only after the
    pending cancel is sent."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = acp_adapter()
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        await wait_until(lambda: bool(agent.prompts))
        turn.cancel()
        with suppress(asyncio.CancelledError):
            await turn
        await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await adapter.on_cleanup(ROOM_ID)
        return list(agent.lifecycle)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_prompting_turn(acp_adapter(), mode),
            expected=[CANCEL_FIRST_SESSION],
        )
        for mode in ENDING_CONTROLS
    },
    "idle-room": StopCase(run=idle, expected=[]),
    "timeout": StopCase(
        run=timed_out, expected=[CANCEL_FIRST_SESSION, CONNECTION_EXIT]
    ),
    "cleanup-after-stop": StopCase(
        run=cleanup_after_stop, expected=[CANCEL_FIRST_SESSION, CONNECTION_EXIT]
    ),
    "next-turn-waits-for-the-previous-stop": StopCase(
        run=next_turn_waits_for_the_previous_stop, expected=[]
    ),
}
