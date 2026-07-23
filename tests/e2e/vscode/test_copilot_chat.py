"""Live L0–L3 cells for the GitHub Copilot in VS Code surface.

Every turn is driver-initiated (see ``driver.turn_prompt``): the room message
is posted first, then the prompt relays it to Copilot Chat with the room id
and the band-tool contract. Assertions are all Band-side — the reply must land
in the right room, from the provisioned identity, via ``band_send_message``.

The reply wait is ``wait_until`` over captured messages, not the baseline
``wait_for_reply``: that barrier keys on the recipient's delivery-status
PROCESSED ack, which only an SDK runtime emits — this surface posts through
band-mcp (REST only), so no delivery ack ever arrives.

L4 usage has no cell here: the surface exposes no per-turn usage signal — the
scorecard carries the rationale (see ``scorecard.USAGE_NA_ROW``).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.vscode.driver import CodeChatDriver, turn_prompt
from tests.e2e.vscode.server import BandMCPServer
from tests.e2e.vscode.settings import VSCodeChatSettings
from tests.e2e.vscode.workspace import workspace_marker_path

pytestmark = [pytest.mark.vscode_chat, pytest.mark.e2e]


def _token() -> str:
    return uuid.uuid4().hex[:6]


async def run_turn(
    capture: ReplyCapture,
    driver: CodeChatDriver,
    *,
    room_id: str,
    identity: ProvisionedAgent,
    sender_name: str,
    message: str,
    instruction: str,
    deadline_s: float,
    new_session: bool = False,
) -> Replies:
    """Submit one relayed-message prompt and wait for the identity's room reply."""
    mark = capture.messages.snapshot()
    await driver.submit_prompt(
        turn_prompt(
            room_id,
            identity.name,
            sender_name=sender_name,
            message=message,
            instruction=instruction,
        ),
        new_session=new_session,
    )

    def replied(_messages: list) -> bool:
        return bool(capture.messages.since(mark).from_sender(identity.id))

    await capture.wait_until(replied, deadline_s=deadline_s)
    return capture.messages.since(mark).from_sender(identity.id)


async def test_participation_reply_round_trip(
    vscode_settings: VSCodeChatSettings,
    copilot_identity: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    driver: CodeChatDriver,
) -> None:
    """L0: a room message reaches Copilot and its reply lands back in the room.

    The token is high-entropy, so the reply containing it proves the relayed
    message flowed through the turn and back via ``band_send_message`` — not
    model invention.
    """
    token = _token()
    room_id = await resource_manager.provision_room(
        title="e2e-vscode-participation", participants=[copilot_identity.id]
    )
    async with reply_capture(room_id) as capture:
        text = f"Ping — the echo token is {token}."
        await user_ops.send_message(
            room_id,
            text,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        replies = await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=text,
            instruction="Reply to this message, repeating its echo token exactly.",
            deadline_s=vscode_settings.vscode_chat_timeout,
        )
    replies.assert_contains_any([token])


async def test_original_functions_retained(
    vscode_settings: VSCodeChatSettings,
    copilot_identity: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    driver: CodeChatDriver,
    vscode_workspace: Path,
    judge,
) -> None:
    """L1: one turn exercises a native VS Code function (create a workspace
    file) AND a platform tool (``band_get_participants``) — the surface keeps
    its original capabilities while participating."""
    token = _token()
    filename = f"notes-{token}.txt"
    room_id = await resource_manager.provision_room(
        title="e2e-vscode-functions", participants=[copilot_identity.id]
    )
    async with reply_capture(room_id) as capture:
        text = (
            f"Please set up a note file for this task and tell me who is "
            f"in this room. The file token is {token}."
        )
        await user_ops.send_message(
            room_id,
            text,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        replies = await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=text,
            instruction=(
                f"First create a file named '{filename}' in the workspace root "
                f"containing exactly the token {token}. Then look up the room "
                f"roster with band_get_participants and reply listing every "
                f"participant's name and confirming the file was created."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
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
        transcript="\n".join(reply.content for reply in replies),
    )
    assert verdict.passed, verdict.reasoning


async def test_multi_participant_echo_peer(
    vscode_settings: VSCodeChatSettings,
    copilot_identity: ProvisionedAgent,
    resource_manager: ResourceManager,
    reply_capture: CaptureFactory,
    driver: CodeChatDriver,
    judge,
) -> None:
    """L0 multi-participant: a peer agent's message drives a turn and the reply
    engages the peer — the surface participates beyond 1:1 user chats."""
    token = _token()
    echo = await resource_manager.provision_agent("echo")
    room_id = await resource_manager.provision_room(
        title="e2e-vscode-peers", participants=[copilot_identity.id, echo.id]
    )
    async with reply_capture(room_id) as capture:
        text = f"ECHO: {token}"
        await resource_manager.peer(echo).send_message(
            room_id,
            text,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        replies = await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name=echo.name,
            message=text,
            instruction=(
                "Another agent posted this in the room. Reply addressing that "
                "agent and repeat its echo token exactly."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
        )

    replies.assert_contains_any([token])
    # Label both sides for the judge: the reply alone reads like an echo
    # message itself and is unjudgeable without the peer's message.
    transcript = f"Peer agent {echo.name} posted: {text}\n" + "\n".join(
        f"Reply from the agent under test: {reply.content}" for reply in replies
    )
    verdict = await judge(
        criteria=(
            "The agent under test replied to the peer agent's echo message, "
            "engaging with it (addressing the peer and/or repeating its token)."
        ),
        transcript=transcript,
    )
    assert verdict.passed, verdict.reasoning


@pytest.mark.timeout(extra=360)  # two full live turns
async def test_recall_across_chat_sessions(
    vscode_settings: VSCodeChatSettings,
    copilot_identity: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    driver: CodeChatDriver,
) -> None:
    """L2 persistence: a fact stored in turn 1 survives to a fresh chat session.

    band-mcp exposes no room-history tool, so cross-session recall flows
    through the platform memory tools — the fact is stored with
    ``band_store_memory`` and must come back via ``band_list_memories`` in a
    session that never saw it.
    """
    fact = f"codename-{_token()}"
    room_id = await resource_manager.provision_room(
        title="e2e-vscode-recall", participants=[copilot_identity.id]
    )
    async with reply_capture(room_id) as capture:
        seed = f"For later reference: the project codename is {fact}."
        await user_ops.send_message(
            room_id,
            seed,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=seed,
            instruction=(
                f"Store the project codename in your Band memory with "
                f"band_store_memory, recording which room it belongs to — "
                f"content like 'project codename for room {room_id}: {fact}'. "
                f"Then confirm in the room that it is saved."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
        )
        # Store-side barrier: separates a store failure (band-mcp/tool call
        # never landed a record) from a retrieval failure in the next turn.
        (await capture.memory(copilot_identity)).stored.assert_stored(content=fact)

        ask = "What is the project codename?"
        await user_ops.send_message(
            room_id,
            ask,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        replies = await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=ask,
            instruction=(
                "Recall the project codename from your Band memory using "
                "band_list_memories (and band_get_memory if needed) — the "
                "record for THIS room (match the chat_id above); the memory "
                "store may hold unrelated records. Do not guess — reply "
                "stating the codename exactly."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
            new_session=True,
        )
    replies.assert_contains_any([fact])


@pytest.mark.timeout(extra=700)  # three full live turns across two rooms
async def test_no_leak_between_rooms(
    vscode_settings: VSCodeChatSettings,
    copilot_identity: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    driver: CodeChatDriver,
) -> None:
    """L2 isolation: room-scoped facts stay scoped — room B's answer names room
    B's marker and never room A's.

    Each ``code chat`` invocation is a fresh chat session and band-mcp has no
    history tool, so room context lives in agent-wide platform memory. The
    seeds record each marker *keyed by its room id*; isolation is then the
    retrieval staying scoped to the asking room — pulling the sibling room's
    marker instead is exactly the leak this cell exists to catch.
    """
    token_a, token_b = f"alpha-{_token()}", f"bravo-{_token()}"
    room_a = await resource_manager.provision_room(
        title="e2e-vscode-isolation-a", participants=[copilot_identity.id]
    )
    room_b = await resource_manager.provision_room(
        title="e2e-vscode-isolation-b", participants=[copilot_identity.id]
    )

    async def seed(room_id: str, capture: ReplyCapture, token: str) -> None:
        text = f"The marker for THIS room is {token}."
        await user_ops.send_message(
            room_id,
            text,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=text,
            instruction=(
                f"Store this room's marker with band_store_memory, recording "
                f"which room it belongs to — content like 'marker for room "
                f"{room_id}: {token}'. Then verify with band_list_memories "
                f"that the record exists (store it again if not), and only "
                f"then acknowledge it in the room."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
        )
        # Store-side barrier (see test_recall_across_chat_sessions).
        (await capture.memory(copilot_identity)).stored.assert_stored(content=token)

    async with reply_capture(room_a) as capture_a:
        await seed(room_a, capture_a, token_a)
    async with reply_capture(room_b) as capture_b:
        await seed(room_b, capture_b, token_b)

        ask = "Which marker belongs to THIS room?"
        await user_ops.send_message(
            room_b,
            ask,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        replies = await run_turn(
            capture_b,
            driver,
            room_id=room_b,
            identity=copilot_identity,
            sender_name="the user",
            message=ask,
            instruction=(
                "Look up the stored markers with band_list_memories and reply "
                "with the one recorded for THIS room (match the chat_id above). "
                "Never mention markers recorded for other rooms."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
            new_session=True,
        )
    replies.assert_contains_any([token_b])
    replies.assert_contains_none([token_a])


@pytest.mark.timeout(extra=360)  # two live turns plus a bridge restart
async def test_restart_recall_and_function(
    vscode_settings: VSCodeChatSettings,
    baseline_settings: BaselineSettings,
    copilot_identity: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    driver: CodeChatDriver,
    band_mcp: BandMCPServer,
    vscode_workspace: Path,
) -> None:
    """L3: the platform bridge (band-mcp) restarts between turns and the surface
    still recalls the stored fact AND keeps its native function (file creation).

    A full VS Code window restart stays a documented manual variant in the
    README — the bridge restart covers the platform side deterministically.
    """
    fact = f"phase-{_token()}"
    filename = f"restart-{_token()}.txt"
    room_id = await resource_manager.provision_room(
        title="e2e-vscode-restart", participants=[copilot_identity.id]
    )
    async with reply_capture(room_id) as capture:
        seed = f"Remember across restarts: the deploy phase is {fact}."
        await user_ops.send_message(
            room_id,
            seed,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=seed,
            instruction=(
                "Store the deploy phase in your Band memory with "
                "band_store_memory, then confirm in the room."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
        )

        await band_mcp.restart()

        ask = "After the maintenance window: what is the deploy phase?"
        await user_ops.send_message(
            room_id,
            ask,
            mention_id=copilot_identity.id,
            mention_name=copilot_identity.name,
        )
        replies = await run_turn(
            capture,
            driver,
            room_id=room_id,
            identity=copilot_identity,
            sender_name="the user",
            message=ask,
            instruction=(
                f"Recall the deploy phase from your Band memory with "
                f"band_list_memories — do not guess — and reply stating it. "
                f"Also create a file named '{filename}' in the workspace root "
                f"containing that phase, to confirm your editor tools still work."
            ),
            deadline_s=vscode_settings.vscode_chat_timeout,
            new_session=True,
        )

    replies.assert_contains_any([fact])
    marker = workspace_marker_path(vscode_workspace, filename)
    assert marker.exists(), f"Copilot did not create {filename} after the restart"
