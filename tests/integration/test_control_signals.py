"""Integration tests for control signals (interrupt / stop / play).

These exercise the one thing the unit tests mock out: the real platform->SDK
round trip. A real ``agent.control`` push emitted by the platform must reach
``BandLink._on_control`` -> ``AgentRuntime.handle_control`` -> the per-room
``ExecutionContext`` and take effect.

Design notes:
- A real ``AgentRuntime`` is driven with a *controllable* ``on_execute`` handler
  (no LLM) so cycle timing is deterministic — this stays an integration test,
  not an e2e/LLM test.
- The room is **user-owned** so the user may issue room-scope stop/play
  (``POST /me/chats/{id}/agents/{stop,play}``). Interrupt is agent-scope
  (``POST /me/agents/{id}/executions/{exec}/interrupt``) and requires the user
  to own the agent; the test skips cleanly if that authorization is absent.
- The control REST endpoints are not in the generated human client yet
  (regeneration tracked separately), so they are called via raw ``httpx``,
  mirroring the documented control-endpoint contract. Swap to the typed
  client once regenerated.

Gated on ``BAND_API_KEY`` + ``BAND_API_KEY_USER``; not run in CI.

Run with:
    uv run pytest tests/integration/test_control_signals.py -v -s
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import pytest_asyncio
from band_rest import ChatMessageRequest
from band_rest.types import ChatMessageRequestMentionsItem

from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime
from tests.conftest import BlockingHandler
from tests.integration.conftest import (
    get_api_key,
    get_base_url,
    get_user_api_key,
    get_ws_url,
    requires_api,
    requires_user_api,
    wait_until,
)

logger = logging.getLogger(__name__)

_API = "/api/v1"


# --------------------------------------------------------------------------- #
# Raw HTTP helpers (user key) for the control endpoints not yet in the client
# --------------------------------------------------------------------------- #


def _user_headers() -> dict[str, str]:
    return {"X-API-Key": get_user_api_key() or ""}


async def _post(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(f"{get_base_url()}{path}", headers=_user_headers())


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.get(f"{get_base_url()}{path}", headers=_user_headers())


async def _send_user_mention(
    user_api_client, chat_id: str, agent_id: str, text: str
) -> str:
    """Post a message from the user that @mentions the agent; return message id."""
    resp = await user_api_client.human_api_messages.send_my_chat_message(
        chat_id,
        message=ChatMessageRequest(
            content=text,
            mentions=[ChatMessageRequestMentionsItem(id=agent_id)],
        ),
    )
    return resp.data.id


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def control_runtime(shared_user_owned_room, shared_agent1_info):
    """A live AgentRuntime for the agent with a controllable handler.

    Wires ``link.on_control`` exactly as PlatformRuntime does. Always resumes the
    room and stops the runtime on teardown so a failed test can't leave the agent
    parked in a stopped state.
    """
    if shared_user_owned_room is None or shared_agent1_info is None:
        pytest.skip("user-owned room/agent unavailable")

    agent_id = shared_agent1_info.id
    link = BandLink(
        agent_id=agent_id,
        api_key=get_api_key() or "",
        ws_url=get_ws_url(),
        rest_url=get_base_url(),
    )
    # Non-blocking by default: the stop/play test needs cycles to complete so
    # the replayed message lands in ``completed``. The interrupt test flips
    # ``handler.block = True`` to make a cycle hang mid-flight.
    handler = BlockingHandler(block=False)
    runtime = AgentRuntime(link=link, agent_id=agent_id, on_execute=handler)
    link.on_control = runtime.handle_control  # mirror PlatformRuntime wiring

    await runtime.start()
    await asyncio.sleep(2.0)  # let presence subscribe to the room
    try:
        yield runtime, handler, agent_id, shared_user_owned_room
    finally:
        try:
            await _post(f"{_API}/me/chats/{shared_user_owned_room}/agents/play")
        except Exception:  # noqa: BLE001 - best-effort un-park
            logger.warning("teardown play failed", exc_info=True)
        await runtime.stop()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@requires_api
@requires_user_api
@pytest.mark.asyncio(loop_scope="session")
class TestControlSignalsIntegration:
    async def test_stop_suppresses_then_play_replays(
        self, control_runtime, user_api_client
    ):
        """Stop silences the agent in the room; play replays the missed mention."""
        runtime, handler, agent_id, chat_id = control_runtime

        # STOP (room-scope; the user owns this room)
        stop = await _post(f"{_API}/me/chats/{chat_id}/agents/stop")
        if stop.status_code == 404:
            pytest.skip("control endpoints not deployed on this platform")
        assert stop.status_code == 200, f"stop failed: {stop.status_code} {stop.text}"

        # A mention sent while stopped must NOT be processed by the handler.
        stopped_msg_id = await _send_user_mention(
            user_api_client, chat_id, agent_id, "ping while stopped — please stay quiet"
        )
        await asyncio.sleep(10.0)
        assert stopped_msg_id not in handler.completed, (
            "agent processed a message while stopped"
        )

        # PLAY -> the platform replays the queued mention; the SDK catches up
        # via /next and the handler now processes it.
        play = await _post(f"{_API}/me/chats/{chat_id}/agents/play")
        assert play.status_code == 200, f"play failed: {play.status_code} {play.text}"

        replayed = await wait_until(
            lambda: stopped_msg_id in handler.completed, timeout=90.0
        )
        assert replayed, "play did not replay the message missed while stopped"

    async def test_interrupt_aborts_in_flight_cycle(
        self, control_runtime, user_api_client
    ):
        """Interrupt cancels a cycle already in flight; nothing is delivered."""
        runtime, handler, agent_id, chat_id = control_runtime

        # Make sure we start un-stopped, then arm the handler to hang mid-cycle.
        await _post(f"{_API}/me/chats/{chat_id}/agents/play")
        handler.block = True
        handler.started.clear()

        msg_id = await _send_user_mention(
            user_api_client, chat_id, agent_id, "begin a long task and keep working"
        )
        assert await wait_until(handler.started.is_set, timeout=90.0), (
            "cycle never started — cannot test interrupt"
        )

        # Interrupt is agent-scope and needs an execution_id; it requires the
        # user to own the agent. Skip cleanly if not authorized.
        ex = await _get(f"{_API}/me/agents/{agent_id}/executions")
        if ex.status_code in (401, 403, 404):
            pytest.skip("user not authorized for agent-scope interrupt")
        executions = ex.json().get("data") or []
        if not executions:
            pytest.skip("no executions available to target")

        # The agent-scope signal fans out to all of the agent's rooms, so any
        # execution id cancels the in-flight cycle in our room.
        interrupt = await _post(
            f"{_API}/me/agents/{agent_id}/executions/{executions[0]['id']}/interrupt"
        )
        assert interrupt.status_code == 200, (
            f"interrupt failed: {interrupt.status_code} {interrupt.text}"
        )

        assert await wait_until(handler.cancelled.is_set, timeout=45.0), (
            "in-flight cycle was not interrupted"
        )
        assert msg_id not in handler.completed, (
            "interrupted cycle still delivered output"
        )
