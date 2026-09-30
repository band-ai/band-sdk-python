"""Manual approval, driven through a room: the scripted model asks for native
tools, people answer in chat, and the test reads what the room saw."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE,
    APPROVAL_RESOLVED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    APPROVAL_UNAUTHORIZED_MESSAGE,
)
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision, ToolCall

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

ADMIN = {"id": "admin-1", "name": "Admin"}
DECLINED = "User declined tool use"
LIST_FILES = ModelDecision.call("Bash", command="ls")
WRITE_NOTE = ModelDecision.call("Write", file_path="notes.md", content="todo")


def prompt(token: str, summary: str) -> str:
    return APPROVAL_REQUESTED_TEMPLATE.format(summary=summary, token=token)


def resolved(token: str, decision: str) -> str:
    return APPROVAL_RESOLVED_TEMPLATE.format(token=token, decision=decision)


async def test_a_room_answers_each_gated_tool_while_its_commands_stay_local(
    claude_room: OpenRoom,
) -> None:
    """A shell command and a file write (which acceptEdits would otherwise
    auto-approve) both wait on the room; listing, status, a wrong token and a
    bare approve are answered locally and never reach the model."""
    room = await claude_room(approval_mode="manual")
    room.claude.script([LIST_FILES, WRITE_NOTE, room.model_reply("Listed, no note.")])

    await room.send("List the files, then jot a note")
    await room.send("/approvals")
    await room.send("/status")
    await room.send("/approve a-9")
    await room.send("/approve")
    await room.until_said("Approval requested", times=2)
    await room.send("/decline a-2")
    await room.settled()

    chat = room.chat
    status = chat.pop(2)
    assert chat == [
        prompt("a-1", "Bash: `ls`"),
        "Pending approvals:\n- `a-1`: Bash: `ls` (0s ago)",
        "Unknown approval token `a-9`. Available: `a-1`.",
        resolved("a-1", "accept"),
        prompt("a-2", "Write: notes.md"),
        resolved("a-2", "decline"),
        "Listed, no note.",
    ]
    assert "- approval_mode: `manual`\n- pending_approvals: 1" in status
    assert room.tool_outputs["Bash"] == "Bash ran"
    assert room.tool_outputs["Write"] == DECLINED
    assert len(room.claude.prompts) == 1
    assert room.failures == []


async def test_a_busy_room_evicts_the_oldest_ask_and_asks_for_a_token(
    claude_room: OpenRoom,
) -> None:
    """Three parallel tool calls against room for two: the oldest is declined
    without a notice, and a bare approve then names the two still open."""
    room = await claude_room(approval_mode="manual", max_pending_approvals_per_room=2)
    room.claude.script(
        [
            ModelDecision(
                tool_calls=(
                    ToolCall("Bash", {"command": "ls"}),
                    ToolCall("Write", {"file_path": "notes.md", "content": "todo"}),
                    ToolCall(
                        "Edit",
                        {"file_path": "app.py", "old_string": "a", "new_string": "b"},
                    ),
                )
            ),
            room.model_reply("Tidied up."),
        ]
    )

    await room.send("Tidy up")
    await room.until_said("Approval requested", times=3)
    await room.send("/approve")
    await room.send("/approve a-1")
    await room.send("/approve a-2")
    await room.send("/decline a-3")
    await room.settled()

    assert room.chat == [
        prompt("a-1", "Bash: `ls`"),
        prompt("a-2", "Write: notes.md"),
        prompt("a-3", "Edit: app.py"),
        "Multiple pending approvals — please specify a token: `a-2`, `a-3`",
        "Unknown approval token `a-1`. Available: `a-2`, `a-3`.",
        resolved("a-2", "accept"),
        resolved("a-3", "decline"),
        "Tidied up.",
    ]
    assert room.tool_outputs["Bash"] == DECLINED
    assert room.tool_outputs["Write"] == "Write ran"
    assert room.tool_outputs["Edit"] == DECLINED
    assert room.failures == []


@pytest.mark.parametrize(
    ("allowlist", "admin_decides"),
    [({"admin-1"}, True), (set(), False)],
    ids=["admins-only", "nobody"],
)
async def test_only_allowlisted_senders_decide_but_anyone_can_list(
    claude_room: OpenRoom, allowlist: set[str], admin_decides: bool
) -> None:
    """However a stranger names the approval, they are refused every time
    rather than guided to a token; an empty allowlist admits nobody, not
    everybody."""
    room = await claude_room(
        approval_mode="manual", approval_authorized_senders=allowlist
    )
    room.claude.script([LIST_FILES, room.model_reply("Listed.")])

    await room.send("List the files")
    await room.send("/decline a-1")
    await room.send("/decline")
    await room.send("/decline a-9")
    await room.send("/approvals")
    await room.send("/approve a-1", sender=ADMIN)
    if admin_decides:
        await room.settled()

    admin_outcome = (
        [resolved("a-1", "accept"), "Listed."]
        if admin_decides
        else [APPROVAL_UNAUTHORIZED_MESSAGE]
    )
    assert room.chat == [
        prompt("a-1", "Bash: `ls`"),
        *[APPROVAL_UNAUTHORIZED_MESSAGE] * 3,
        "Pending approvals:\n- `a-1`: Bash: `ls` (0s ago)",
        *admin_outcome,
    ]


@pytest.mark.looptime
@pytest.mark.parametrize(
    ("decision", "bash_output"),
    [("accept", "Bash ran"), ("decline", "Approval timed out, tool use declined")],
)
async def test_an_unanswered_ask_times_out_to_the_configured_decision(
    claude_room: OpenRoom, decision: str, bash_output: str
) -> None:
    """A reply landing while the timeout notice is still being sent is told
    the token is gone, not "resolved"."""
    room = await claude_room(
        approval_mode="manual",
        approval_wait_timeout_s=60,
        approval_timeout_decision=decision,
    )
    room.claude.script([LIST_FILES, room.model_reply("Done waiting.")])
    timeout_notice = room.tools.hold_message("timed out")

    await room.send("List the files")
    async with timeout_notice:
        await room.send("/approve a-1")
    await room.settled()

    assert room.chat == [
        prompt("a-1", "Bash: `ls`"),
        "Unknown approval token `a-1`. Available: none.",
        APPROVAL_TIMED_OUT_TEMPLATE.format(token="a-1", decision=decision),
        "Done waiting.",
    ]
    assert room.tool_outputs["Bash"] == bash_output


async def test_answers_stand_however_a_flaky_room_connection_fares(
    claude_room: OpenRoom,
) -> None:
    """An undelivered prompt nobody answered declines at once and leaves
    nothing pending; one answered mid-send honors the answer; and a
    "resolved" notice that fails to send never undoes the accept."""
    room = await claude_room(approval_mode="manual")
    network_down = RuntimeError("network down")
    room.claude.script(
        [LIST_FILES, room.model_reply("Could not ask.")],
        [LIST_FILES, room.model_reply("Listed.")],
        [LIST_FILES, room.model_reply("Listed again.")],
    )

    unheard_prompt = room.tools.hold_message("Approval requested", error=network_down)
    delivery = room.send_in_background("List the files")
    async with unheard_prompt:
        pass
    await delivery
    await room.send("/approvals")
    assert room.tool_outputs["Bash"] == (
        "Could not deliver approval prompt, tool use declined"
    )

    answered_prompt = room.tools.hold_message("Approval requested", error=network_down)
    delivery = room.send_in_background("Try again")
    async with answered_prompt:
        await room.send("/approve")
    await delivery
    await room.settled()
    assert room.tool_outputs["Bash"] == "Bash ran"

    failing_notice = room.tools.hold_message("resolved", error=network_down)
    await room.send("Once more")
    await room.send("/approve a-3")
    async with failing_notice:
        pass
    await room.settled()
    assert room.tool_outputs["Bash"] == "Bash ran"

    assert room.chat == [
        "Could not ask.",
        "No pending approvals.",
        resolved("a-2", "accept"),
        "Listed.",
        prompt("a-3", "Bash: `ls`"),
        "Listed again.",
    ]
    assert room.failures == []


async def test_leaving_a_room_drops_only_its_asks_and_tokens_keep_counting(
    claude_room: OpenRoom,
) -> None:
    """Tokens count per room; leaving one room closes its session without
    touching the other's pending ask, a rejoin continues the room's token
    sequence, and stopping the agent closes every session."""
    room = await claude_room(approval_mode="manual")
    other = room.beside("room-2")
    room.claude.script(
        [LIST_FILES, room.model_reply("Listed.")],
        [WRITE_NOTE, other.model_reply("Noted.")],
        [LIST_FILES, room.model_reply("Listed after rejoining.")],
    )

    await room.send("List the files")
    await other.send("Jot a note")
    await room.leave()
    await other.send("/approve")
    await other.settled()
    await room.send("List them again")
    await room.adapter.cleanup_all()

    assert room.chat == [prompt("a-1", "Bash: `ls`"), prompt("a-2", "Bash: `ls`")]
    assert other.chat == [
        prompt("a-1", "Write: notes.md"),
        resolved("a-1", "accept"),
        "Noted.",
    ]
    assert [session.alive for session in room.claude.sessions] == [False] * 3
    assert room.failures == other.failures == []
