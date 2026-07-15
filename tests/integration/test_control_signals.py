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
import time
from collections.abc import Callable

import httpx
import pytest
import pytest_asyncio
from thenvoi_rest.human_api_chats.types.create_my_chat_room_request_chat import (
    CreateMyChatRoomRequestChat,
)
from thenvoi_rest.types import ParticipantRequest

from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime
from tests.integration.conftest import (
    get_api_key,
    get_base_url,
    get_user_api_key,
    get_ws_url,
    is_room_alive,
    requires_api,
    requires_user_api,
)

logger = logging.getLogger(__name__)

_API = "/api/v1"


# --------------------------------------------------------------------------- #
# Raw HTTP helpers (user key) for endpoints not yet in the generated client
# --------------------------------------------------------------------------- #


def _user_headers() -> dict[str, str]:
    return {"X-API-Key": get_user_api_key() or ""}


async def _post(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(f"{get_base_url()}{path}", headers=_user_headers())


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.get(f"{get_base_url()}{path}", headers=_user_headers())


async def _send_user_mention(chat_id: str, agent_id: str, text: str) -> str:
    """Post a message from the user that @mentions the agent; return message id."""
    body = {"message": {"content": text, "mentions": [{"id": agent_id}]}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{get_base_url()}{_API}/me/chats/{chat_id}/messages",
            headers=_user_headers(),
            json=body,
        )
    assert resp.status_code in (200, 201), (
        f"send failed: {resp.status_code} {resp.text}"
    )
    return resp.json()["data"]["id"]


async def _wait_until(
    pred: Callable[[], bool], *, timeout: float, interval: float = 1.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return pred()


class _ControllableHandler:
    """Stand-in for an adapter's on_execute with deterministic timing.

    Records which message ids it fully processed, signals when a cycle starts,
    and (when ``block`` is set) hangs until cancelled so an interrupt can land
    mid-cycle.
    """

    def __init__(self) -> None:
        self.block = False
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.completed: list[str] = []

    async def __call__(self, ctx, event) -> None:
        payload = getattr(event, "payload", None)
        msg_id = getattr(payload, "id", None)
        self.started.set()
        try:
            if self.block:
                await asyncio.sleep(120)  # hang until interrupted
            if msg_id:
                self.completed.append(msg_id)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def control_room(user_api_client, session_api_client, shared_agent1_info):
    """A **user-owned** chat with the agent as a participant (reused across runs).

    User ownership is required so the user may issue room-scope stop/play. The
    reuse-or-create logic keeps this within the platform's per-agent room limit
    even though the fixture is function-scoped.
    """
    if (
        user_api_client is None
        or session_api_client is None
        or shared_agent1_info is None
    ):
        return None

    agent_id = shared_agent1_info.id

    # Reuse a live user-owned room that already has the agent (10-room limit).
    chats = await user_api_client.human_api_chats.list_my_chats()
    for room in reversed(chats.data or []):
        if not await is_room_alive(session_api_client, room.id):
            continue
        parts = await user_api_client.human_api_participants.list_my_chat_participants(
            room.id
        )
        if any(p.id == agent_id for p in (parts.data or [])):
            logger.info("Reusing user-owned control room: %s", room.id)
            return room.id

    created = await user_api_client.human_api_chats.create_my_chat_room(
        chat=CreateMyChatRoomRequestChat()
    )
    chat_id = created.data.id
    await user_api_client.human_api_participants.add_my_chat_participant(
        chat_id, participant=ParticipantRequest(participant_id=agent_id, role="member")
    )
    logger.info("Created user-owned control room: %s", chat_id)
    return chat_id


@pytest_asyncio.fixture
async def control_runtime(control_room, shared_agent1_info):
    """A live AgentRuntime for the agent with a controllable handler.

    Wires ``link.on_control`` exactly as PlatformRuntime does. Always resumes the
    room and stops the runtime on teardown so a failed test can't leave the agent
    parked in a stopped state.
    """
    if control_room is None or shared_agent1_info is None:
        pytest.skip("control_room/agent unavailable")

    agent_id = shared_agent1_info.id
    link = BandLink(
        agent_id=agent_id,
        api_key=get_api_key() or "",
        ws_url=get_ws_url(),
        rest_url=get_base_url(),
    )
    handler = _ControllableHandler()
    runtime = AgentRuntime(link=link, agent_id=agent_id, on_execute=handler)
    link.on_control = runtime.handle_control  # mirror PlatformRuntime wiring

    await runtime.start()
    await asyncio.sleep(2.0)  # let presence subscribe to the room
    try:
        yield runtime, handler, agent_id, control_room
    finally:
        try:
            await _post(f"{_API}/me/chats/{control_room}/agents/play")
        except Exception:  # noqa: BLE001 - best-effort un-park
            logger.warning("teardown play failed", exc_info=True)
        await runtime.stop()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@requires_api
@requires_user_api
class TestControlSignalsIntegration:
    async def test_stop_suppresses_then_play_replays(self, control_runtime):
        """Stop silences the agent in the room; play replays the missed mention."""
        runtime, handler, agent_id, chat_id = control_runtime

        # STOP (room-scope; the user owns this room)
        stop = await _post(f"{_API}/me/chats/{chat_id}/agents/stop")
        if stop.status_code == 404:
            pytest.skip("control endpoints not deployed on this platform")
        assert stop.status_code == 200, f"stop failed: {stop.status_code} {stop.text}"

        # A mention sent while stopped must NOT be processed by the handler.
        stopped_msg_id = await _send_user_mention(
            chat_id, agent_id, "ping while stopped — please stay quiet"
        )
        await asyncio.sleep(10.0)
        assert stopped_msg_id not in handler.completed, (
            "agent processed a message while stopped"
        )

        # PLAY -> the platform replays the queued mention; the SDK catches up
        # via /next and the handler now processes it.
        play = await _post(f"{_API}/me/chats/{chat_id}/agents/play")
        assert play.status_code == 200, f"play failed: {play.status_code} {play.text}"

        replayed = await _wait_until(
            lambda: stopped_msg_id in handler.completed, timeout=90.0
        )
        assert replayed, "play did not replay the message missed while stopped"

    async def test_interrupt_aborts_in_flight_cycle(self, control_runtime):
        """Interrupt cancels a cycle already in flight; nothing is delivered."""
        runtime, handler, agent_id, chat_id = control_runtime

        # Make sure we start un-stopped, then arm the handler to hang mid-cycle.
        await _post(f"{_API}/me/chats/{chat_id}/agents/play")
        handler.block = True
        handler.started.clear()

        msg_id = await _send_user_mention(
            chat_id, agent_id, "begin a long task and keep working"
        )
        assert await _wait_until(handler.started.is_set, timeout=90.0), (
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

        assert await _wait_until(handler.cancelled.is_set, timeout=45.0), (
            "in-flight cycle was not interrupted"
        )
        assert msg_id not in handler.completed, (
            "interrupted cycle still delivered output"
        )
