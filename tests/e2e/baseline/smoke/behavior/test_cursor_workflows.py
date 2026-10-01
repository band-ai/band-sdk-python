"""Live Cursor ACP work across human decisions, project edits, and room restart."""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.client.streaming import DeliveryStatus
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.types import Capability, MessageType
from band.integrations.acp.cursor import (
    DECISION_RESOLVED_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from band.integrations.acp.room_emitter import ACP_SESSION_CLOSED_EVENT
from band.runtime.tools.effects import turn_effect
from band.runtime.tools.types import TurnEffect
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvalroom import ApprovalRoom
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    Outcome,
    cursor_test_adapter,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.observations.memories import Memories
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    from band.adapters.cursor_acp import CursorACPAdapter
    from band.integrations.acp.session_config import (
        ACPConfigRequest,
        SessionConfigResolver,
    )

REPAIR_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
PLAN_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
RECOVERY_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=4)
CURSOR_MODE_OPTION_ID = "mode"
MAX_PERMISSION_REQUESTS = 8
PROJECT_TEST_TIMEOUT_S = 30
TURN_POLL_INTERVAL_S = 0.5
BACKUP_FILE_NAME = "backup.txt"
PLAN_REQUEST = re.compile(
    rf"(?P<plan>.*) needs approval\. Reply .*?"
    rf"{re.escape(ROOM_COMMAND)} {CursorCommandWord.ACCEPT} (?P<token>[\w-]+)",
    re.DOTALL,
)


@dataclass(frozen=True)
class TurnCheckpoint:
    cursor: int
    closed_count: int
    text_ids: frozenset[str]


async def _closed_turn_count(room: ApprovalRoom) -> int:
    tasks = await room.capture.tasks(sender_id=room.agent.id)
    return sum(task.content == ACP_SESSION_CLOSED_EVENT for task in tasks)


async def _start_turn(room: ApprovalRoom, text: str) -> TurnCheckpoint:
    closed_count = await _closed_turn_count(room)
    messages = await room.user_ops.list_messages(
        room.room_id, message_type=MessageType.TEXT
    )
    cursor = await room.say(text)
    return TurnCheckpoint(cursor, closed_count, frozenset(m.id for m in messages))


async def _wait_for_turn_close(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> None:
    async def wait() -> None:
        while await _closed_turn_count(room) <= checkpoint.closed_count:
            await asyncio.sleep(TURN_POLL_INTERVAL_S)

    try:
        await asyncio.wait_for(wait(), timeout=room.budget.deadline_s)
    except TimeoutError:
        pytest.fail(
            f"Cursor turn did not close after the decision: "
            f"{room.said_since(checkpoint.cursor)}"
        )


async def _new_agent_text(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> list[str]:
    messages = await room.user_ops.list_messages(
        room.room_id, message_type=MessageType.TEXT
    )
    return [
        message.content or ""
        for message in messages
        if message.id not in checkpoint.text_ids and message.sender_id == room.agent.id
    ]


def _project(root: Path) -> Path:
    source = root / "calculator.py"
    source.write_text("def total(a: int, b: int) -> int:\n    return a - b\n")
    (root / "test_calculator.py").write_text(
        "import unittest\n"
        "from calculator import total\n\n"
        "class CalculatorTest(unittest.TestCase):\n"
        "    def test_total(self) -> None:\n"
        "        self.assertEqual(total(2, 3), 5)\n"
    )
    return source


def _store_completion_memory(marker: str) -> str:
    return (
        "After the work succeeds, call band_store_memory exactly once with "
        f"content including {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}, segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.AGENT.value}."
    )


async def _stored_memories(capture: ReplyCapture, agent: ProvisionedAgent) -> Memories:
    return (await capture.memory(agent)).stored


def _project_state(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    }


async def _run_project_command(root: Path, *args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *args,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=PROJECT_TEST_TIMEOUT_S
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        pytest.fail(
            f"Project command did not finish within {PROJECT_TEST_TIMEOUT_S}s: {args}"
        )
    assert process.returncode is not None
    return process.returncode, (stdout + stderr).decode(errors="replace")


async def _project_tests(root: Path) -> tuple[int, str]:
    return await _run_project_command(root, "-m", "unittest", "discover")


async def _assert_repaired_project(root: Path) -> None:
    result, output = await _run_project_command(
        root,
        "-c",
        "from calculator import total; actual = total(2, 3); "
        "assert actual == 5, f'{actual} != 5'",
    )
    assert result == 0, output
    result, output = await _project_tests(root)
    assert result == 0, output


def _select_mode(mode: str) -> SessionConfigResolver:
    async def resolve(request: ACPConfigRequest) -> dict[str, str]:
        # The ACP extra is absent from the crewai and parlant collection venvs.
        from acp.schema import SessionConfigOptionSelect  # noqa: PLC0415

        from band.integrations.acp.session_config import (  # noqa: PLC0415
            flatten_select_options,
        )

        option = next(
            (
                item
                for item in request.config_options
                if item.id == CURSOR_MODE_OPTION_ID
            ),
            None,
        )
        assert isinstance(option, SessionConfigOptionSelect), (
            f"Cursor did not advertise a selectable {CURSOR_MODE_OPTION_ID} mode: "
            f"{request.config_options}"
        )
        offered = {item.value for item in flatten_select_options(option.options)}
        assert mode in offered, f"Cursor did not advertise {mode}: {offered}"
        return {CURSOR_MODE_OPTION_ID: mode}

    return resolve


async def _plan_request(room: ApprovalRoom, *, since: int) -> tuple[str, str]:
    def find_request() -> tuple[str, str] | None:
        for message in room.capture.messages.since(since):
            if match := PLAN_REQUEST.search(message.content or ""):
                return match["token"], match["plan"]
        return None

    try:
        await room.capture.wait_until(
            lambda _messages: find_request() is not None,
            deadline_s=room.budget.deadline_s,
        )
    except TimeoutError:
        pytest.fail(
            f"Cursor did not send cursor/create_plan in plan mode; "
            f"room messages: {room.said_since(since)}"
        )
    request = find_request()
    assert request is not None
    return request


async def _decide_plan(
    room: ApprovalRoom,
    *,
    checkpoint: TurnCheckpoint,
    accept: bool,
    reply_marker: str,
) -> str:
    token, plan = await _plan_request(room, since=checkpoint.cursor)
    word = CursorCommandWord.ACCEPT if accept else CursorCommandWord.REJECT
    after_decision = await room.say(f"{ROOM_COMMAND} {word} {token}")
    notice = DECISION_RESOLVED_TEMPLATE.format(kind="plan", token=token)
    await room.shown(notice, since=after_decision)
    try:
        await room.capture.wait_until(
            lambda items, cursor=after_decision: any(
                PLAN_REQUEST.search(item.content or "")
                or reply_marker in (item.content or "")
                for item in items[cursor:]
            ),
            deadline_s=room.budget.deadline_s,
        )
    except TimeoutError:
        pytest.fail(
            f"Cursor did not finish the plan decision: "
            f"{room.said_since(after_decision)}"
        )
    await _wait_for_turn_close(room, checkpoint)
    requests = [
        match["token"]
        for content in await _new_agent_text(room, checkpoint)
        if (match := PLAN_REQUEST.search(content))
    ]
    assert requests == [token], (
        f"Cursor requested another plan without separate human review: {requests}"
    )
    return plan


async def _decide_permissions_until_reply(
    room: ApprovalRoom,
    *,
    checkpoint: TurnCheckpoint,
    reply_marker: str,
    deny_first_tool: str | None = None,
) -> int:
    [request] = await room.requests(1, since=checkpoint.cursor)
    denied = 0
    for _ in range(MAX_PERMISSION_REQUESTS):
        if deny_first_tool is not None and denied == 0:
            assert deny_first_tool in request["tool"], (
                f"Cursor did not request the expected project action: {request['tool']}"
            )
            outcome = Outcome.DECLINE
        else:
            outcome = Outcome.APPROVE
        denied += outcome is Outcome.DECLINE
        after_decision = await room.say(room.dialect.reply(outcome, request))
        await room.shown(
            room.dialect.notice(outcome, request).text, since=after_decision
        )
        try:
            messages = await room.capture.wait_until(
                lambda items, cursor=after_decision: (
                    bool(room.dialect.find_requests(items[cursor:]))
                    or any(
                        reply_marker in (item.content or "") for item in items[cursor:]
                    )
                ),
                deadline_s=room.budget.deadline_s,
            )
        except TimeoutError:
            pytest.fail(
                f"Cursor did not finish after {outcome}: "
                f"{room.said_since(after_decision)}"
            )
        requests = room.dialect.find_requests(messages[after_decision:])
        if not requests:
            await _wait_for_turn_close(room, checkpoint)
            return denied
        requested_tool = requests[0]["tool"].partition(":")[0]
        if (
            deny_first_tool is not None
            and turn_effect(requested_tool) is not TurnEffect.REPLY
        ):
            pytest.fail(
                "Cursor requested another permission after the denied action: "
                f"{room.said_since(checkpoint.cursor)}"
            )
        request = requests[0]
    pytest.fail(
        f"Cursor requested permission more than {MAX_PERMISSION_REQUESTS} times: "
        f"{room.said_since(checkpoint.cursor)}"
    )


@per_adapter(Adapter.CURSOR_ACP)
@pytest.mark.timeout(extra=REPAIR_BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_repairs_a_failing_project_after_a_human_gate(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    source = _project(root)
    initial_exit, initial_output = await _project_tests(root)
    assert initial_exit != 0, initial_output
    original = source.read_text()
    original_tests = (root / "test_calculator.py").read_bytes()
    original_state = _project_state(root)
    marker = unique_marker("cursor-repair-event")
    report = unique_marker("cursor-repair-report")
    denied_reply = unique_marker("cursor-repair-denied")
    setup = AgentSetup(root, REPAIR_BUDGET.deadline_s * 2)
    agent = await cell.provision(label="cursor-repair")
    room_id = await cell.resources.provision_room(
        title="e2e-cursor-repair", participants=[agent.id]
    )

    def adapter() -> CursorACPAdapter:
        return cursor_test_adapter(
            cell.settings,
            setup,
            custom_section="Use a shell tool for project writes. Keep replies short.",
            capabilities={Capability.MEMORY},
        )

    async with (
        running_agent(agent, adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = ApprovalRoom(
            agent,
            Adapter.CURSOR_ACP,
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            REPAIR_BUDGET,
        )
        checkpoint = await _start_turn(
            room,
            "Before reading or changing any project file, use one shell command "
            f"to copy calculator.py to {BACKUP_FILE_NAME}. Do not edit the project until "
            "that command is decided. Only after the copy "
            f"succeeds, {_store_completion_memory(marker)} If I "
            f"deny the copy, do not retry; reply with {denied_reply}.",
        )
        denied = await _decide_permissions_until_reply(
            room,
            checkpoint=checkpoint,
            reply_marker=denied_reply,
            deny_first_tool=BACKUP_FILE_NAME,
        )
        assert denied >= 1
        assert _project_state(root) == original_state
        assert len(await _stored_memories(capture, agent)) == 0

        checkpoint = await _start_turn(
            room,
            "Skip the backup. Diagnose and repair calculator.py so the "
            "existing unittest passes. Use one shell tool call for the edit "
            "and test run. "
            f"{_store_completion_memory(marker)} Then report the result "
            f"with {report}.",
        )
        assert source.read_text() == original
        await _decide_permissions_until_reply(
            room,
            checkpoint=checkpoint,
            reply_marker=report,
        )
        assert source.read_text() != original
        assert (root / "test_calculator.py").read_bytes() == original_tests
        await _assert_repaired_project(root)
        stored = await _stored_memories(capture, agent)
        assert len(stored) == 1
        stored.assert_stored(content=marker)


@per_adapter(Adapter.CURSOR_ACP)
@pytest.mark.timeout(extra=PLAN_BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_plan_stays_read_only_until_separately_approved(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    source = _project(root)
    original = source.read_text()
    original_state = _project_state(root)
    rejected_reply = unique_marker("cursor-plan-rejected")
    accepted_reply = unique_marker("cursor-plan-accepted")
    implementation_reply = unique_marker("cursor-plan-implemented")
    setup = AgentSetup(root, PLAN_BUDGET.deadline_s * 2)
    identity = await cell.provision(label="cursor-plan")
    room_id = await cell.resources.provision_room(
        title="e2e-cursor-plan", participants=[identity.id]
    )

    def plan_adapter() -> CursorACPAdapter:
        return cursor_test_adapter(
            cell.settings,
            setup,
            approval_mode="auto_accept",
            plan_mode="manual",
            resolve_session_config=_select_mode("plan"),
        )

    async with (
        running_agent(identity, plan_adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = ApprovalRoom(
            identity,
            Adapter.CURSOR_ACP,
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            PLAN_BUDGET,
        )
        checkpoint = await _start_turn(
            room,
            "Read calculator.py and test_calculator.py in this workspace. "
            "Plan a multi-step repair of the failing calculator test. Submit "
            "the plan for human review with cursor/create_plan, not just a "
            "chat outline. Do not implement yet. If I reject it, do not "
            f"request another plan; reply with {rejected_reply}.",
        )
        rejected_plan = await _decide_plan(
            room,
            checkpoint=checkpoint,
            accept=False,
            reply_marker=rejected_reply,
        )
        assert _project_state(root) == original_state

    async with (
        running_agent(identity, plan_adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = ApprovalRoom(
            identity,
            Adapter.CURSOR_ACP,
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            PLAN_BUDGET,
        )
        checkpoint = await _start_turn(
            room,
            "Revise the plan to keep the change limited to calculator.py and "
            "its test, then request human review with cursor/create_plan again. "
            f"If I accept it, reply with {accepted_reply}. Do not implement yet.",
        )
        accepted_plan = await _decide_plan(
            room,
            checkpoint=checkpoint,
            accept=True,
            reply_marker=accepted_reply,
        )
        assert accepted_plan != rejected_plan
        assert "calculator.py" in accepted_plan
        assert _project_state(root) == original_state

    agent_adapter = cursor_test_adapter(
        cell.settings,
        setup,
        resolve_session_config=_select_mode("agent"),
        custom_section="Use a shell tool for project writes. Keep replies short.",
    )
    async with (
        running_agent(identity, agent_adapter, cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = ApprovalRoom(
            identity,
            Adapter.CURSOR_ACP,
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            PLAN_BUDGET,
        )
        checkpoint = await _start_turn(
            room,
            "Implement the accepted calculator repair, run its unittest in "
            "the same shell tool call, and report the result with "
            f"{implementation_reply}.",
        )
        assert source.read_text() == original
        await _decide_permissions_until_reply(
            room,
            checkpoint=checkpoint,
            reply_marker=implementation_reply,
        )
    assert source.read_text() != original
    final_state = _project_state(root)
    changed = {
        path
        for path in original_state.keys() | final_state.keys()
        if original_state.get(path) != final_state.get(path)
    }
    assert changed <= {"calculator.py", "test_calculator.py"}, changed
    await _assert_repaired_project(root)


@per_adapter(Adapter.CURSOR_ACP)
@pytest.mark.timeout(extra=RECOVERY_BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_sequential_rooms_keep_their_work_after_restart(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Path,
) -> None:
    identity = await cell.provision(label="cursor-recovery")
    rooms = [
        await cell.resources.provision_room(
            title=f"e2e-cursor-recovery-{index}", participants=[identity.id]
        )
        for index in range(2)
    ]
    workspaces = {
        room: (tmp_path / str(index)).resolve() for index, room in enumerate(rooms)
    }
    for path in workspaces.values():
        path.mkdir()
    markers = {
        room: unique_marker(f"cursor-room-{index}") for index, room in enumerate(rooms)
    }
    setup = AgentSetup(tmp_path.resolve(), RECOVERY_BUDGET.deadline_s * 2)

    def adapter() -> CursorACPAdapter:
        return cursor_test_adapter(
            cell.settings,
            setup,
            approval_mode="auto_accept",
            workspace_for_room=lambda room_id: str(workspaces[room_id]),
            custom_section="Work only in the current room workspace. Keep replies short.",
            capabilities={Capability.MEMORY},
        )

    handled: dict[str, str] = {}
    boundaries: dict[str, datetime] = {}
    async with running_agent(identity, adapter(), cell.settings):
        for expected_count, room_id in enumerate(rooms, start=1):
            marker = markers[room_id]
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    "Create note.txt in this room's workspace containing the "
                    f"marker {marker}. {_store_completion_memory(marker)} "
                    "Include the marker in your reply.",
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, deadline_s=RECOVERY_BUDGET.deadline_s
                )
                replies.assert_contains_any([marker])
                assert (workspaces[room_id] / "note.txt").read_text().strip() == marker
                stored = await _stored_memories(capture, identity)
                assert len(stored) == expected_count
                assert len(stored.where(content=marker)) == 1
                handled[room_id] = mid
                boundaries[room_id] = capture.turn_boundary()

    offline = {
        room_id: await user_ops.send_message(
            room_id,
            "Recall the marker from our earlier conversation in this room and "
            "reply with it. Do not read or write files or call Band tools.",
            mention_id=identity.id,
            mention_name=identity.name,
        )
        for room_id in rooms
    }
    async with (
        reply_capture(rooms[0]) as first,
        reply_capture(rooms[1]) as second,
    ):
        captures = dict(zip(rooms, (first, second), strict=True))
        async with running_agent(identity, adapter(), cell.settings):
            for room_id in rooms:
                replies = await captures[room_id].wait_for_reply(
                    offline[room_id],
                    identity.id,
                    deadline_s=RECOVERY_BUDGET.deadline_s,
                )
                replies.assert_contains_any([markers[room_id]])
                other = next(other for other in rooms if other != room_id)
                replies.assert_contains_none([markers[other]])
        stored = await _stored_memories(first, identity)
        assert len(stored) == len(rooms)
        for room_id in rooms:
            calls = await captures[room_id].tool_calls(
                sender_id=identity.id,
                since=boundaries[room_id],
                include_memory=True,
            )
            assert not calls, f"Cursor used tools to recall the room marker: {calls}"
            assert DeliveryStatus.PROCESSING not in captures[room_id].delivery_history(
                handled[room_id], identity.id
            )
            marker = markers[room_id]
            assert len(stored.where(content=marker)) == 1
            assert (workspaces[room_id] / "note.txt").read_text().strip() == marker
