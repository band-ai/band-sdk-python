# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""Verify a deployed AgentCore three-agent orchestration demo end-to-end.

This is manual verification tooling for the ``examples/agentcore/`` demo, not an
automated test — it drives a **live** Band platform and the externally-deployed
demo, so it never runs in CI. Use it after you have stood the demo up to confirm
it actually orchestrates.

It exercises the acceptance scenario: a user asks ``@personal_assistant`` a
question needing both ``@weather`` and ``@math``; PA adds them to the room, asks
each what it needs, and posts a final synthesized answer. It runs that flow once
in a single room, then twice concurrently in two rooms to confirm the bridge's
per-room session isolation (no cross-bleed between rooms).

Prerequisites:

1. ``BAND_REST_URL``, ``BAND_WS_URL``, ``BAND_API_KEY_USER`` — a user account on
   the target platform (this tool connects as the user, not an agent).
2. The demo deployed and running externally:
   - Three AgentCore Runtimes (weather / math / personal_assistant).
   - The bridge running with ``BAND_BRIDGE_AGENTS`` pointing at the three
     identities and their runtime ARNs (see ``run_agentcore.py``).
3. ``AGENTCORE_DEMO_PA_AGENT_ID`` — the personal_assistant's Band agent UUID, so
   this tool knows whom to @-mention. (PA recruits the other two at runtime.)
   ``AGENTCORE_DEMO_PA_AGENT_NAME`` optionally overrides the mention name
   (default ``personal_assistant``).

Config is read from the environment; values in ``.env.test`` at the repo root
are loaded automatically. Run with::

    AGENTCORE_DEMO_PA_AGENT_ID=<uuid> \\
        uv run python examples/agentcore/verify_demo.py

Exits 0 if all flows pass, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("verify_demo")

# Load .env.test from the repo root so BAND_* vars are available without export.
load_dotenv(Path(__file__).resolve().parents[2] / ".env.test", override=False)

PA_AGENT_ID_ENV = "AGENTCORE_DEMO_PA_AGENT_ID"


class TrackingWebSocketClient:
    """Async-context-manager wrapper that tracks joined rooms and leaves them on exit.

    Use as ``async with TrackingWebSocketClient(ws) as client:`` — every room it
    joins is left on exit, so callers never hand-roll a ``try/finally``. Uses a
    set to avoid duplicate leave calls when a room is left and rejoined. Only the
    methods used here are delegated — no ``__getattr__`` proxy.
    """

    def __init__(self, ws: object) -> None:
        self._ws = ws
        self._joined_rooms: set[str] = set()

    async def __aenter__(self) -> TrackingWebSocketClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.cleanup_channels()

    async def join_chat_room_channel(
        self, chat_room_id: str, on_message_created: Callable[..., Awaitable[None]]
    ) -> object:
        result = await self._ws.join_chat_room_channel(chat_room_id, on_message_created)
        self._joined_rooms.add(chat_room_id)
        return result

    async def leave_chat_room_channel(self, chat_room_id: str) -> object:
        result = await self._ws.leave_chat_room_channel(chat_room_id)
        self._joined_rooms.discard(chat_room_id)
        return result

    async def cleanup_channels(self) -> None:
        """Leave all tracked channels. Best-effort; errors are logged."""
        for room_id in list(self._joined_rooms):
            try:
                await self._ws.leave_chat_room_channel(room_id)
            except Exception:
                logger.debug("Failed to leave room %s during cleanup", room_id)
        self._joined_rooms.clear()


def require_env(name: str, hint: str) -> str:
    """Return a required env var or raise ValueError with an actionable hint."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required — {hint}")
    return value


async def send_trigger_message(
    client: object,
    room_id: str,
    content: str,
    mention_name: str,
    mention_id: str,
) -> str:
    """Send a message as the user that triggers the agent's processing loop.

    Sends with user credentials so the sender is the user (agents skip
    self-authored messages) and @mentions PA so the platform routes it.
    """
    from band_rest import ChatMessageRequest
    from band_rest.types import ChatMessageRequestMentionsItem as Mention

    response = await client.human_api_messages.send_my_chat_message(
        room_id,
        message=ChatMessageRequest(
            content=f"@{mention_name} {content}",
            mentions=[Mention(id=mention_id, name=mention_name)],
        ),
    )
    message_id = response.data.id
    logger.info("Sent message %s to room %s: %s", message_id, room_id, content[:80])
    return message_id


@asynccontextmanager
async def listening_for_agent_responses(
    ws_client: TrackingWebSocketClient,
    room_id: str,
    timeout: float,
) -> AsyncGenerator[Callable[[], Awaitable[list]], None]:
    """Subscribe to a room before sending, then yield a waiter for agent replies."""
    received: list = []
    event = asyncio.Event()

    async def handler(payload: object) -> None:
        if payload.sender_type == "Agent" and payload.message_type == "text":
            received.append(payload)
            logger.info("Agent reply in room %s: %s", room_id, payload.content[:80])
            event.set()

    await ws_client.join_chat_room_channel(room_id, handler)
    try:

        async def wait() -> list:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except TimeoutError:
                logger.warning(
                    "Timeout after %.0fs waiting for a reply in room %s",
                    timeout,
                    room_id,
                )
            return received

        yield wait
    finally:
        await ws_client.leave_chat_room_channel(room_id)


async def create_room_with_pa(user_client: object, pa_agent_id: str, label: str) -> str:
    """Create a fresh chat room and add @personal_assistant to it."""
    from band_rest import CreateMyChatRoomRequestChat
    from band_rest.types import ParticipantRequest

    response = await user_client.human_api_chats.create_my_chat_room(
        chat=CreateMyChatRoomRequestChat(),
    )
    if not response.data:
        raise RuntimeError(f"[{label}] create_my_chat_room returned no data")
    room_id = response.data.id
    await user_client.human_api_participants.add_my_chat_participant(
        chat_id=room_id,
        participant=ParticipantRequest(participant_id=pa_agent_id, role="member"),
    )
    logger.info("Created room %s for demo flow [%s]", room_id, label)
    return room_id


async def run_single_flow(
    *,
    user_client: object,
    ws_client: TrackingWebSocketClient,
    pa_agent_id: str,
    pa_agent_name: str,
    question: str,
    label: str,
    timeout: float,
) -> str:
    """Run one user → PA → peers → PA → user flow; return PA's final reply text."""
    room_id = await create_room_with_pa(user_client, pa_agent_id, label)
    async with listening_for_agent_responses(ws_client, room_id, timeout) as wait:
        await send_trigger_message(
            user_client, room_id, question, pa_agent_name, pa_agent_id
        )
        received = await wait()

    if not received:
        raise RuntimeError(
            f"[{label}] PA never replied — check the bridge is running and the "
            "three AgentCore runtimes are healthy."
        )
    final = received[-1].content
    logger.info("[%s] PA final reply: %s", label, final[:200])
    return final


def check(condition: bool, message: str) -> bool:
    """Log a PASS/FAIL line for one expectation; return the condition."""
    logger.info("%s %s", "PASS" if condition else "FAIL", message)
    return condition


async def verify_single_room(
    user_client: object,
    ws_client: TrackingWebSocketClient,
    pa_agent_id: str,
    pa_agent_name: str,
    timeout: float,
) -> bool:
    """One room: PA recruits @weather and @math, final answer names both cities."""
    logger.info("--- Scenario: single-room orchestration ---")
    final = (
        await run_single_flow(
            user_client=user_client,
            ws_client=ws_client,
            pa_agent_id=pa_agent_id,
            pa_agent_name=pa_agent_name,
            question=(
                "What is the temperature difference now, in percents, "
                "between Tel Aviv and Warsaw?"
            ),
            label="solo",
            timeout=timeout,
        )
    ).lower()
    return check(
        "tel aviv" in final and "warsaw" in final,
        "single-room final reply names both cities",
    )


async def verify_parallel_rooms(
    user_client: object,
    ws_client: TrackingWebSocketClient,
    pa_agent_id: str,
    pa_agent_name: str,
    timeout: float,
) -> bool:
    """Two concurrent rooms: each answer names its own cities, no cross-bleed."""
    logger.info("--- Scenario: two parallel rooms (session isolation) ---")
    reply_a, reply_b = await asyncio.gather(
        run_single_flow(
            user_client=user_client,
            ws_client=ws_client,
            pa_agent_id=pa_agent_id,
            pa_agent_name=pa_agent_name,
            question=(
                "What is the temperature difference now, in percents, "
                "between Tel Aviv and Warsaw?"
            ),
            label="room-A",
            timeout=timeout,
        ),
        run_single_flow(
            user_client=user_client,
            ws_client=ws_client,
            pa_agent_id=pa_agent_id,
            pa_agent_name=pa_agent_name,
            question=(
                "What is the temperature difference now, in percents, "
                "between New York and London?"
            ),
            label="room-B",
            timeout=timeout,
        ),
    )
    a, b = reply_a.lower(), reply_b.lower()
    return all(
        [
            check("tel aviv" in a and "warsaw" in a, "room A names its own cities"),
            check("new york" in b and "london" in b, "room B names its own cities"),
            check("new york" not in a, "room A did not leak room B's cities"),
            check("tel aviv" not in b, "room B did not leak room A's cities"),
        ]
    )


async def main() -> None:
    from band.client.streaming import WebSocketClient
    from band_rest import AsyncRestClient

    rest_url = require_env("BAND_REST_URL", "the target platform's REST base URL")
    ws_url = require_env("BAND_WS_URL", "the target platform's WebSocket URL")
    api_key_user = require_env(
        "BAND_API_KEY_USER", "a user API key to drive and observe the demo"
    )
    pa_agent_id = require_env(
        PA_AGENT_ID_ENV,
        "the personal_assistant's Band agent UUID; deploy the demo first "
        "(see examples/agentcore/README.md)",
    )
    pa_agent_name = os.environ.get("AGENTCORE_DEMO_PA_AGENT_NAME", "personal_assistant")
    timeout = max(float(os.environ.get("E2E_TIMEOUT", "120")), 90.0)  # multi-hop

    user_client = AsyncRestClient(api_key=api_key_user, base_url=rest_url)
    ws = WebSocketClient(ws_url=ws_url, api_key=api_key_user, agent_id=None)

    async with ws, TrackingWebSocketClient(ws) as ws_client:
        results = [
            await verify_single_room(
                user_client, ws_client, pa_agent_id, pa_agent_name, timeout
            ),
            await verify_parallel_rooms(
                user_client, ws_client, pa_agent_id, pa_agent_name, timeout
            ),
        ]

    if all(results):
        logger.info("All demo verification scenarios PASSED")
    else:
        logger.error("Demo verification FAILED — see FAIL lines above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
