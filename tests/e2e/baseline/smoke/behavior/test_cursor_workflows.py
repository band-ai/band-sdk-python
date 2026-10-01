"""Live Cursor ACP work across human decisions, project edits, and room restart."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest
from band_rest import ChatMessage

from band.client.streaming import DeliveryStatus, MessageCreatedPayload
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.types import Capability
from band.integrations.acp.cursor import (
    DECISION_RESOLVED_TEMPLATE,
    PLAN_REQUESTED_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from band.integrations.acp.room_emitter import ACP_SESSION_CLOSED_EVENT
from band.runtime.tools.effects import turn_effect
from band.runtime.tools.types import TurnEffect
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvalroom import (
    TERMINAL_POLL_INTERVAL_S,
    ApprovalRoom,
)
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    Outcome,
    cursor_test_adapter,
    template_pattern,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    unique_marker,
)
from tests.e2e.baseline.timeouts import SlowTurnBudget, slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.observations.memories import Memories
from tests.e2e.baseline.toolkit.observations.tool_calls import MemoryTool
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

TURN_BUDGET_S = BaselineSettings().e2e_timeout
WORKFLOW_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=6)
RECOVERY_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=4)
CURSOR_MODE_OPTION_ID = "mode"
MAX_PERMISSION_REQUESTS = 8
PROJECT_TEST_TIMEOUT_S = 30
BACKUP_FILE_NAME = "backup.txt"
PLAN_REQUEST = template_pattern(PLAN_REQUESTED_TEMPLATE)


@dataclass(frozen=True)
class TurnCheckpoint:
    cursor: int
    # Server time of the room's newest item before the turn; None in an empty room.
    started: datetime | None


async def _room_now(user_ops: UserOps, room_id: str) -> datetime | None:
    latest = await user_ops.list_messages(room_id, limit=1)
    return latest[-1].inserted_at if latest else None


def _posted_after(message: ChatMessage, stamp: datetime | None) -> bool:
    # Strict, so the previous turn's close event at the boundary never counts.
    return stamp is None or (
        message.inserted_at is not None and message.inserted_at > stamp
    )


async def _start_turn(room: ApprovalRoom, text: str) -> TurnCheckpoint:
    started = await _room_now(room.user_ops, room.room_id)
    return TurnCheckpoint(await room.say(text), started)


async def _turn_failure(
    room: ApprovalRoom, checkpoint: TurnCheckpoint, what: str
) -> NoReturn:
    # Adapter failures, such as a rejected session config, arrive only as error events.
    errors = await room.capture.errors(
        sender_id=room.agent.id, since=checkpoint.started
    )
    pytest.fail(
        f"{what}; room messages: {room.said_since(checkpoint.cursor)}; "
        f"agent errors: {[error.content for error in errors]}"
    )


async def _turn_closed(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> bool:
    # A decision releases the triggering message before the detached turn ends,
    # so only the session-closed event marks the turn's end.
    tasks = await room.capture.tasks(sender_id=room.agent.id, since=checkpoint.started)
    return any(
        task.content == ACP_SESSION_CLOSED_EVENT
        and _posted_after(task, checkpoint.started)
        for task in tasks
    )


async def _wait_for_turn_close(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> None:
    async def wait() -> None:
        while not await _turn_closed(room, checkpoint):
            await asyncio.sleep(TERMINAL_POLL_INTERVAL_S)

    try:
        await asyncio.wait_for(wait(), timeout=room.budget.deadline_s)
    except TimeoutError:
        await _turn_failure(room, checkpoint, "Cursor turn did not close")


def _cursor_room(
    agent: ProvisionedAgent,
    room_id: str,
    capture: ReplyCapture,
    user_ops: UserOps,
    budget: SlowTurnBudget,
) -> ApprovalRoom:
    return ApprovalRoom(
        agent,
        Adapter.CURSOR_ACP,
        room_id,
        capture,
        DIALECTS[Adapter.CURSOR_ACP],
        user_ops,
        budget,
    )


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
        f"After the work succeeds, call {MemoryTool.STORE.value} exactly once with "
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
        from band.integrations.acp.session_config import (  # noqa: PLC0415
            find_select,
            select_values,
        )

        option = find_select(request.config_options, CURSOR_MODE_OPTION_ID)
        assert option is not None, (
            f"Cursor did not advertise a selectable {CURSOR_MODE_OPTION_ID} mode: "
            f"{request.config_options}"
        )
        offered = select_values(option)
        assert mode in offered, f"Cursor did not advertise {mode}: {offered}"
        return {CURSOR_MODE_OPTION_ID: mode}

    return resolve


def _plan_requests(messages: Sequence[MessageCreatedPayload]) -> list[re.Match[str]]:
    return [
        match for item in messages if (match := PLAN_REQUEST.search(item.content or ""))
    ]


async def _plan_request(
    room: ApprovalRoom, checkpoint: TurnCheckpoint
) -> re.Match[str]:
    try:
        messages = await room.capture.wait_until(
            lambda items: bool(_plan_requests(items[checkpoint.cursor :])),
            deadline_s=room.budget.deadline_s,
        )
    except TimeoutError:
        await _turn_failure(
            room, checkpoint, "Cursor did not send cursor/create_plan in plan mode"
        )
    return _plan_requests(messages[checkpoint.cursor :])[0]


async def _decide_plan(
    room: ApprovalRoom,
    *,
    checkpoint: TurnCheckpoint,
    word: CursorCommandWord,
    reply_marker: str,
) -> str:
    request = await _plan_request(room, checkpoint)
    token = request["token"]
    after_decision = await room.say(f"{ROOM_COMMAND} {word} {token}")
    notice = DECISION_RESOLVED_TEMPLATE.format(kind="plan", token=token)
    await room.shown(notice, since=after_decision)
    try:
        messages = await room.capture.wait_until(
            lambda items: (
                bool(_plan_requests(items[after_decision:]))
                or any(
                    reply_marker in (item.content or "")
                    for item in items[after_decision:]
                )
            ),
            deadline_s=room.budget.deadline_s,
        )
    except TimeoutError:
        await _turn_failure(room, checkpoint, "Cursor did not finish the plan decision")
    # A repeated request would hold the turn open on a new manual decision.
    repeated = [match["token"] for match in _plan_requests(messages[after_decision:])]
    assert not repeated, (
        f"Cursor requested another plan without separate human review: {repeated}"
    )
    await _wait_for_turn_close(room, checkpoint)
    return request["plan"]


async def _decide_permissions_until_reply(
    room: ApprovalRoom,
    *,
    checkpoint: TurnCheckpoint,
    reply_marker: str,
    deny_first_tool: str | None = None,
) -> None:
    [request] = await room.requests(1, since=checkpoint.cursor)
    for attempt in range(MAX_PERMISSION_REQUESTS):
        outcome = Outcome.APPROVE
        if deny_first_tool is not None and attempt == 0:
            assert deny_first_tool in request["tool"], (
                f"Cursor did not request the expected project action: {request['tool']}"
            )
            outcome = Outcome.DECLINE
        after_decision = await room.decide(outcome, request)
        await room.shown(
            room.dialect.notice(outcome, request).text, since=after_decision
        )
        try:
            await room.capture.wait_until(
                lambda items, cursor=after_decision: (
                    bool(room.unanswered_requests(since=checkpoint.cursor))
                    or any(
                        reply_marker in (item.content or "") for item in items[cursor:]
                    )
                ),
                deadline_s=room.budget.deadline_s,
            )
        except TimeoutError:
            await _turn_failure(
                room, checkpoint, f"Cursor did not finish after {outcome}"
            )
        pending = room.unanswered_requests(since=checkpoint.cursor)
        if not pending:
            await _wait_for_turn_close(room, checkpoint)
            return
        requested_tool = pending[0]["tool"].partition(":")[0]
        if (
            deny_first_tool is not None
            and turn_effect(requested_tool) is not TurnEffect.REPLY
        ):
            pytest.fail(
                "Cursor requested another permission after the denied action: "
                f"{room.said_since(checkpoint.cursor)}"
            )
        request = pending[0]
    pytest.fail(
        f"Cursor requested permission more than {MAX_PERMISSION_REQUESTS} times: "
        f"{room.said_since(checkpoint.cursor)}"
    )


@per_adapter(Adapter.CURSOR_ACP)
@pytest.mark.timeout(extra=WORKFLOW_BUDGET.extra_s)
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
    original_state = _project_state(root)
    marker = unique_marker("cursor-repair-event")
    report = unique_marker("cursor-repair-report")
    denied_reply = unique_marker("cursor-repair-denied")
    setup = AgentSetup(root, WORKFLOW_BUDGET.deadline_s * 2)
    agent = await cell.provision(label="cursor-repair")
    room_id = await cell.resources.provision_room(
        title="e2e-cursor-repair", participants=[agent.id]
    )

    repair_adapter = cursor_test_adapter(
        cell.settings,
        setup,
        custom_section="Use a shell tool for project writes. Keep replies short.",
        capabilities={Capability.MEMORY},
    )
    async with (
        running_agent(agent, repair_adapter, cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = _cursor_room(agent, room_id, capture, user_ops, WORKFLOW_BUDGET)
        checkpoint = await _start_turn(
            room,
            "Before reading or changing any project file, use one shell command "
            f"to copy calculator.py to {BACKUP_FILE_NAME}. Do not edit the project until "
            "that command is decided. Only after the copy "
            f"succeeds, {_store_completion_memory(marker)} If I "
            f"deny the copy, do not retry; reply with {denied_reply}.",
        )
        await _decide_permissions_until_reply(
            room,
            checkpoint=checkpoint,
            reply_marker=denied_reply,
            deny_first_tool=BACKUP_FILE_NAME,
        )
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
        assert (root / "test_calculator.py").read_bytes() == original_state[
            "test_calculator.py"
        ]
        await _assert_repaired_project(root)
        stored = await _stored_memories(capture, agent)
        assert len(stored) == 1
        stored.assert_stored(content=marker)


@per_adapter(Adapter.CURSOR_ACP)
@pytest.mark.timeout(extra=WORKFLOW_BUDGET.extra_s)
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
    setup = AgentSetup(root, WORKFLOW_BUDGET.deadline_s * 2)
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
        room = _cursor_room(identity, room_id, capture, user_ops, WORKFLOW_BUDGET)
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
            word=CursorCommandWord.REJECT,
            reply_marker=rejected_reply,
        )
        assert _project_state(root) == original_state

    async with (
        running_agent(identity, plan_adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = _cursor_room(identity, room_id, capture, user_ops, WORKFLOW_BUDGET)
        checkpoint = await _start_turn(
            room,
            "Revise the plan to keep the change limited to calculator.py and "
            "its test, then request human review with cursor/create_plan again. "
            f"If I accept it, reply with {accepted_reply}. Do not implement yet.",
        )
        accepted_plan = await _decide_plan(
            room,
            checkpoint=checkpoint,
            word=CursorCommandWord.ACCEPT,
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
        room = _cursor_room(identity, room_id, capture, user_ops, WORKFLOW_BUDGET)
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

    # Every turn-1 event is durable once its message is processed.
    recall_since = {room_id: await _room_now(user_ops, room_id) for room_id in rooms}
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
                since=recall_since[room_id],
                include_memory=True,
            )
            assert not calls, f"Cursor used tools to recall the room marker: {calls}"
            assert DeliveryStatus.PROCESSING not in captures[room_id].delivery_history(
                handled[room_id], identity.id
            )
            marker = markers[room_id]
            assert len(stored.where(content=marker)) == 1
            assert (workspaces[room_id] / "note.txt").read_text().strip() == marker
