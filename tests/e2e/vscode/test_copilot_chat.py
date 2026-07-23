"""Live L0–L3 cells for the GitHub Copilot in VS Code surface.

Every cell speaks through ``SurfaceRoom`` (``rooms.py``): someone says
something in the room, the driver relays it to Copilot Chat, and the agent's
reply is asserted **Band-side** — it must land in the right room, from the
provisioned identity, via ``band_send_message``. Tokens are high-entropy so a
reply containing one proves the round trip, not model invention.

L4 usage has no cell here: the surface exposes no per-turn usage signal — the
scorecard carries the rationale (see ``scorecard.USAGE_NA_ROW``).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.vscode.rooms import transcript
from tests.e2e.vscode.server import BandMCPServer
from tests.e2e.vscode.workspace import workspace_marker_path

pytestmark = [pytest.mark.vscode_chat, pytest.mark.e2e]


def _token() -> str:
    return uuid.uuid4().hex[:6]


async def test_participation_reply_round_trip(surface_room) -> None:
    """L0: a room message reaches Copilot and its reply lands back in the room."""
    token = _token()
    async with surface_room("participation") as room:
        replies = await room.user_turn(
            f"Ping — the echo token is {token}.",
            instruction="Reply to this message, repeating its echo token exactly.",
        )
    replies.assert_contains_any([token])


async def test_original_functions_retained(
    surface_room, vscode_workspace: Path, judge
) -> None:
    """L1: one turn exercises a native VS Code function (create a workspace
    file) AND a platform tool (``band_get_participants``) — the surface keeps
    its original capabilities while participating."""
    token = _token()
    filename = f"notes-{token}.txt"
    async with surface_room("functions") as room:
        replies = await room.user_turn(
            f"Please set up a note file for this task and tell me who is "
            f"in this room. The file token is {token}.",
            instruction=(
                f"First create a file named '{filename}' in the workspace root "
                f"containing exactly the token {token}. Then look up the room "
                f"roster with band_get_participants and reply listing every "
                f"participant's name and confirming the file was created."
            ),
        )

    marker = workspace_marker_path(vscode_workspace, filename)
    assert marker.exists(), f"Copilot did not create {filename} in the workspace"
    assert token in marker.read_text()

    verdict = await judge(
        criteria=(
            "The reply lists the chat room's participants (at least the agent "
            "itself or the human user) and confirms that the requested note "
            "file was created."
        ),
        transcript=transcript(replies),
    )
    assert verdict.passed, verdict.reasoning


async def test_multi_participant_echo_peer(
    surface_room, resource_manager: ResourceManager, judge
) -> None:
    """L0 multi-participant: a peer agent's message drives a turn and the reply
    engages the peer — the surface participates beyond 1:1 user chats."""
    token = _token()
    echo = await resource_manager.provision_agent("echo")
    echo_message = f"ECHO: {token}"

    async with surface_room("peers", participants=(echo.id,)) as room:
        replies = await room.peer_turn(
            echo,
            echo_message,
            instruction=(
                "Another agent posted this in the room. Reply addressing that "
                "agent and repeat its echo token exactly."
            ),
        )

    replies.assert_contains_any([token])
    verdict = await judge(
        criteria=(
            "The agent under test replied to the peer agent's echo message, "
            "engaging with it (addressing the peer and/or repeating its token)."
        ),
        transcript=transcript(replies, peer_message=echo_message),
    )
    assert verdict.passed, verdict.reasoning


@pytest.mark.timeout(extra=360)  # two full live turns
async def test_recall_across_chat_sessions(surface_room) -> None:
    """L2 persistence: a fact stored in turn 1 survives to a fresh chat session.

    band-mcp exposes no room-history tool, so cross-session recall flows
    through the platform memory tools — the fact is stored room-keyed with
    ``band_store_memory`` and must come back via ``band_list_memories`` in a
    session that never saw it.
    """
    fact = f"codename-{_token()}"
    async with surface_room("recall") as room:
        await room.remember(
            f"For later reference: the project codename is {fact}.",
            record=f"project codename for room {room.room_id}: {fact}",
        )

        replies = await room.user_turn(
            "What is the project codename?",
            instruction=(
                "Recall the project codename from your Band memory using "
                "band_list_memories (and band_get_memory if needed) — the "
                "record for THIS room (match the chat_id above); the memory "
                "store may hold unrelated records. Do not guess — reply "
                "stating the codename exactly."
            ),
            new_session=True,
        )
    replies.assert_contains_any([fact])


@pytest.mark.timeout(extra=700)  # three full live turns across two rooms
async def test_no_leak_between_rooms(surface_room) -> None:
    """L2 isolation: room-scoped facts stay scoped — room B's answer names room
    B's marker and never room A's.

    Each ``code chat`` invocation is a fresh chat session and band-mcp has no
    history tool, so room context lives in agent-wide platform memory. The
    seeds record each marker *keyed by its room id*; isolation is then the
    retrieval staying scoped to the asking room — pulling the sibling room's
    marker instead is exactly the leak this cell exists to catch.
    """
    token_a, token_b = f"alpha-{_token()}", f"bravo-{_token()}"

    async def seed(room, token: str) -> None:
        await room.remember(
            f"The marker for THIS room is {token}.",
            record=f"marker for room {room.room_id}: {token}",
        )

    async with surface_room("isolation-a") as room_a:
        await seed(room_a, token_a)
    async with surface_room("isolation-b") as room_b:
        await seed(room_b, token_b)

        replies = await room_b.user_turn(
            "Which marker belongs to THIS room?",
            instruction=(
                "Look up the stored markers with band_list_memories and reply "
                "with the one recorded for THIS room (match the chat_id above). "
                "Never mention markers recorded for other rooms."
            ),
            new_session=True,
        )
    replies.assert_contains_any([token_b])
    replies.assert_contains_none([token_a])


@pytest.mark.timeout(extra=360)  # two live turns plus a bridge restart
async def test_restart_recall_and_function(
    surface_room, band_mcp: BandMCPServer, vscode_workspace: Path
) -> None:
    """L3: the platform bridge (band-mcp) restarts between turns and the surface
    still recalls the stored fact AND keeps its native function (file creation).

    A full VS Code window restart stays a documented manual variant in the
    README — the bridge restart covers the platform side deterministically.
    """
    fact = f"phase-{_token()}"
    filename = f"restart-{_token()}.txt"
    async with surface_room("restart") as room:
        await room.remember(
            f"Remember across restarts: the deploy phase is {fact}.",
            record=f"deploy phase for room {room.room_id}: {fact}",
        )

        await band_mcp.restart()

        replies = await room.user_turn(
            "After the maintenance window: what is the deploy phase?",
            instruction=(
                f"Recall the deploy phase from your Band memory with "
                f"band_list_memories — the record for THIS room (match the "
                f"chat_id above); do not guess — and reply stating it. "
                f"Also create a file named '{filename}' in the workspace root "
                f"containing that phase, to confirm your editor tools still work."
            ),
            new_session=True,
        )

    replies.assert_contains_any([fact])
    marker = workspace_marker_path(vscode_workspace, filename)
    assert marker.exists(), f"Copilot did not create {filename} after the restart"
