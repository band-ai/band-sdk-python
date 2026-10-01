"""Live Cursor ACP work across human decisions, project edits, and room restart."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypeVar

import pytest
from band_rest import ChatMessage

from band.client.streaming import DeliveryStatus
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.types import Capability
from band.integrations.acp.client_profiles import CURSOR_CREATE_PLAN_METHOD
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
    APPROVAL_LOG_LEVEL,
    TERMINAL_POLL_INTERVAL_S,
    ApprovalRoom,
)
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    Outcome,
    cursor_test_adapter,
    find_requests,
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

logger = logging.getLogger(__name__)

TURN_BUDGET_S = BaselineSettings().e2e_timeout
WORKFLOW_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=6)
RECOVERY_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=4)
CURSOR_MODE_OPTION_ID = "mode"
CURSOR_PLAN_MODE = "plan"
CURSOR_AGENT_MODE = "agent"
MAX_PERMISSION_REQUESTS = 8
PROJECT_TEST_TIMEOUT_S = 30
SOURCE_FILE = "calculator.py"
TEST_FILE = "test_calculator.py"
BACKUP_FILE = "backup.txt"
NOTE_FILE = "note.txt"
# Operands and expected sum once calculator.py is repaired.
REPAIRED_TOTAL = (2, 3, 5)
PROJECT_WRITE_PROMPT = "Use a shell tool for project writes. Keep replies short."
PLAN_REQUEST = template_pattern(PLAN_REQUESTED_TEMPLATE)
T = TypeVar("T")


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


async def _within_turn(
    room: ApprovalRoom, checkpoint: TurnCheckpoint, what: str, wait: Awaitable[T]
) -> T:
    try:
        async with asyncio.timeout(room.budget.deadline_s):
            return await wait
    except TimeoutError:
        await _turn_failure(room, checkpoint, what)


async def _turn_closed(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> bool:
    # A decision releases the triggering message before the detached turn ends,
    # so only the session-closed event marks the turn's end.
    tasks = await room.capture.tasks(sender_id=room.agent.id, since=checkpoint.started)
    return any(
        task.content == ACP_SESSION_CLOSED_EVENT
        and _posted_after(task, checkpoint.started)
        for task in tasks
    )


async def _until_closed(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> None:
    while not await _turn_closed(room, checkpoint):
        await asyncio.sleep(TERMINAL_POLL_INTERVAL_S)


async def _next_request_or_close(
    room: ApprovalRoom, checkpoint: TurnCheckpoint
) -> re.Match[str] | None:
    while True:
        if pending := room.unanswered_requests(since=checkpoint.cursor):
            return pending[0]
        if await _turn_closed(room, checkpoint):
            # A permission can land while we were checking for close.
            if pending := room.unanswered_requests(since=checkpoint.cursor):
                return pending[0]
            return None
        await asyncio.sleep(TERMINAL_POLL_INTERVAL_S)


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


def _agent_setup(workdir: Path, budget: SlowTurnBudget) -> AgentSetup:
    # A pending decision outlives the test's wait, so the test's own failure fires first.
    return AgentSetup(workdir, budget.deadline_s * 2)


def _project(root: Path) -> Path:
    left, right, expected = REPAIRED_TOTAL
    source = root / SOURCE_FILE
    source.write_text("def total(a: int, b: int) -> int:\n    return a - b\n")
    (root / TEST_FILE).write_text(
        "import unittest\n"
        f"from {source.stem} import total\n\n"
        "class CalculatorTest(unittest.TestCase):\n"
        "    def test_total(self) -> None:\n"
        f"        self.assertEqual(total({left}, {right}), {expected})\n"
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


def _assert_project_unchanged(root: Path, original_state: dict[str, bytes]) -> None:
    assert _project_state(root) == original_state


def _assert_changed_within(
    root: Path, original_state: dict[str, bytes], allowed: set[str]
) -> None:
    final_state = _project_state(root)
    changed = {
        path
        for path in original_state.keys() | final_state.keys()
        if original_state.get(path) != final_state.get(path)
    }
    assert changed <= allowed, changed


async def _run_project_command(root: Path, *args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *args,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(), timeout=PROJECT_TEST_TIMEOUT_S
        )
    except TimeoutError:
        process.kill()
        output, _ = await process.communicate()
        pytest.fail(
            f"Project command did not finish within {PROJECT_TEST_TIMEOUT_S}s: {args}\n"
            f"{output.decode(errors='replace')}"
        )
    assert process.returncode is not None
    return process.returncode, output.decode(errors="replace")


async def _project_tests(root: Path) -> tuple[int, str]:
    return await _run_project_command(root, "-m", "unittest", "discover")


async def _assert_repaired_project(root: Path) -> None:
    left, right, expected = REPAIRED_TOTAL
    result, output = await _run_project_command(
        root,
        "-c",
        f"from {Path(SOURCE_FILE).stem} import total; "
        f"actual = total({left}, {right}); "
        f"assert actual == {expected}, f'{{actual}} != {expected}'",
    )
    assert result == 0, output
    result, output = await _project_tests(root)
    assert result == 0, output


def _normalized_plan(plan: str) -> str:
    return " ".join(plan.split())


def _assert_revised_scoped_plan(accepted: str, rejected: str) -> None:
    """Accepted plan must revise the rejected one and name the scoped project files."""
    assert _normalized_plan(accepted) != _normalized_plan(rejected), (
        "Accepted plan is only whitespace-different from the rejected plan: "
        f"{accepted!r}"
    )
    assert SOURCE_FILE in accepted and TEST_FILE in accepted, (
        f"Accepted plan did not name the scoped files {SOURCE_FILE} and "
        f"{TEST_FILE}: {accepted!r}"
    )


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


async def _plan_request(
    room: ApprovalRoom, checkpoint: TurnCheckpoint
) -> re.Match[str]:
    messages = await _within_turn(
        room,
        checkpoint,
        f"Cursor did not send {CURSOR_CREATE_PLAN_METHOD} in plan mode",
        room.capture.wait_until(
            lambda items: bool(find_requests(PLAN_REQUEST, items[checkpoint.cursor :])),
            deadline_s=room.budget.deadline_s,
        ),
    )
    return find_requests(PLAN_REQUEST, messages[checkpoint.cursor :])[0]


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
    logger.log(
        APPROVAL_LOG_LEVEL,
        "Plan decision adapter=%s request=%s outcome=%s",
        room.adapter_id,
        token,
        word,
    )
    notice = DECISION_RESOLVED_TEMPLATE.format(kind="plan", token=token)
    await _within_turn(
        room,
        checkpoint,
        "Cursor did not resolve the plan decision",
        room.shown(notice, since=after_decision),
    )
    messages = await _within_turn(
        room,
        checkpoint,
        "Cursor did not finish the plan decision",
        room.capture.wait_until(
            lambda items: (
                bool(find_requests(PLAN_REQUEST, items[after_decision:]))
                or any(
                    reply_marker in (item.content or "")
                    for item in items[after_decision:]
                )
            ),
            deadline_s=room.budget.deadline_s,
        ),
    )
    # A repeated request would hold the turn open on a new manual decision.
    repeated = [
        match["token"]
        for match in find_requests(PLAN_REQUEST, messages[after_decision:])
    ]
    assert not repeated, (
        f"Cursor requested another plan without separate human review: {repeated}"
    )
    await _within_turn(
        room, checkpoint, "Cursor turn did not close", _until_closed(room, checkpoint)
    )
    return request["plan"]


async def _permission_request(
    room: ApprovalRoom, checkpoint: TurnCheckpoint
) -> re.Match[str]:
    [request] = await _within_turn(
        room,
        checkpoint,
        "Cursor did not request permission",
        room.requests(1, since=checkpoint.cursor),
    )
    return request


async def _decide_permissions_until_closed(
    room: ApprovalRoom,
    request: re.Match[str],
    *,
    checkpoint: TurnCheckpoint,
    reply_marker: str,
    deny_first_tool: str | None = None,
) -> None:
    """Answer every permission request until the turn closes, then expect the reply.

    A request can follow the reply (a memory write, a read-back), and an unanswered
    one holds the turn open, so the reply alone does not end the decisions.
    """
    for attempt in range(MAX_PERMISSION_REQUESTS):
        outcome = Outcome.APPROVE
        if deny_first_tool is not None and attempt == 0:
            assert deny_first_tool in request["tool"], (
                f"Cursor did not request the expected project action: {request['tool']}"
            )
            outcome = Outcome.DECLINE
        after_decision = await room.decide(outcome, request)
        await _within_turn(
            room,
            checkpoint,
            f"Cursor did not confirm the {outcome} decision",
            room.shown(
                room.dialect.notice(outcome, request).text, since=after_decision
            ),
        )
        pending = await _within_turn(
            room,
            checkpoint,
            f"Cursor did not finish after {outcome}",
            _next_request_or_close(room, checkpoint),
        )
        if pending is None:
            await _within_turn(
                room,
                checkpoint,
                "Cursor closed the turn without its reply",
                room.shown(reply_marker, since=checkpoint.cursor),
            )
            return
        requested_tool = pending["tool"].partition(":")[0]
        if (
            deny_first_tool is not None
            and turn_effect(requested_tool) is not TurnEffect.REPLY
        ):
            pytest.fail(
                "Cursor requested another permission after the denied action: "
                f"{room.said_since(checkpoint.cursor)}"
            )
        request = pending
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
    setup = _agent_setup(root, WORKFLOW_BUDGET)
    agent = await cell.provision(label="cursor-repair")
    room_id = await cell.resources.provision_room(
        title="e2e-cursor-repair", participants=[agent.id]
    )

    repair_adapter = cursor_test_adapter(
        cell.settings,
        setup,
        custom_section=PROJECT_WRITE_PROMPT,
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
            f"to copy {SOURCE_FILE} to {BACKUP_FILE}. Do not edit the project until "
            "that command is decided. Only after the copy "
            f"succeeds, {_store_completion_memory(marker)} If I "
            f"deny the copy, do not retry; reply with {denied_reply}.",
        )
        request = await _permission_request(room, checkpoint)
        await _decide_permissions_until_closed(
            room,
            request,
            checkpoint=checkpoint,
            reply_marker=denied_reply,
            deny_first_tool=BACKUP_FILE,
        )
        _assert_project_unchanged(root, original_state)
        assert len(await _stored_memories(capture, agent)) == 0

        checkpoint = await _start_turn(
            room,
            f"Skip the backup. Diagnose and repair {SOURCE_FILE} so the "
            "existing unittest passes. Use one shell tool call for the edit "
            "and test run. "
            f"{_store_completion_memory(marker)} Then report the result "
            f"with {report}.",
        )
        request = await _permission_request(room, checkpoint)
        # Nothing may change while the human gate is pending.
        _assert_project_unchanged(root, original_state)
        await _decide_permissions_until_closed(
            room, request, checkpoint=checkpoint, reply_marker=report
        )
        assert source.read_text() != original
        assert (root / TEST_FILE).read_bytes() == original_state[TEST_FILE]
        _assert_changed_within(root, original_state, {SOURCE_FILE})
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
    setup = _agent_setup(root, WORKFLOW_BUDGET)
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
            resolve_session_config=_select_mode(CURSOR_PLAN_MODE),
        )

    async with (
        running_agent(identity, plan_adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = _cursor_room(identity, room_id, capture, user_ops, WORKFLOW_BUDGET)
        checkpoint = await _start_turn(
            room,
            f"Read {SOURCE_FILE} and {TEST_FILE} in this workspace. "
            "Plan a multi-step repair of the failing calculator test. Submit "
            f"the plan for human review with {CURSOR_CREATE_PLAN_METHOD}, not just a "
            "chat outline. Do not implement yet. If I reject it, do not "
            f"request another plan; reply with {rejected_reply}.",
        )
        rejected_plan = await _decide_plan(
            room,
            checkpoint=checkpoint,
            word=CursorCommandWord.REJECT,
            reply_marker=rejected_reply,
        )
        _assert_project_unchanged(root, original_state)

    async with (
        running_agent(identity, plan_adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = _cursor_room(identity, room_id, capture, user_ops, WORKFLOW_BUDGET)
        checkpoint = await _start_turn(
            room,
            f"Revise the plan to keep the change limited to {SOURCE_FILE} and "
            f"{TEST_FILE}, then request human review with {CURSOR_CREATE_PLAN_METHOD} again. "
            f"If I accept it, reply with {accepted_reply}. Do not implement yet.",
        )
        accepted_plan = await _decide_plan(
            room,
            checkpoint=checkpoint,
            word=CursorCommandWord.ACCEPT,
            reply_marker=accepted_reply,
        )
        _assert_revised_scoped_plan(accepted_plan, rejected_plan)
        _assert_project_unchanged(root, original_state)

    agent_adapter = cursor_test_adapter(
        cell.settings,
        setup,
        resolve_session_config=_select_mode(CURSOR_AGENT_MODE),
        custom_section=PROJECT_WRITE_PROMPT,
    )
    async with (
        running_agent(identity, agent_adapter, cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = _cursor_room(identity, room_id, capture, user_ops, WORKFLOW_BUDGET)
        checkpoint = await _start_turn(
            room,
            "Implement the accepted calculator repair plan below, run its "
            "unittest in the same shell tool call, and report the result with "
            f"{implementation_reply}.\n\nAccepted plan:\n{accepted_plan}",
        )
        request = await _permission_request(room, checkpoint)
        # Nothing may change while the human gate is pending.
        _assert_project_unchanged(root, original_state)
        await _decide_permissions_until_closed(
            room, request, checkpoint=checkpoint, reply_marker=implementation_reply
        )
    assert source.read_text() != original
    _assert_changed_within(root, original_state, {SOURCE_FILE, TEST_FILE})
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
    setup = _agent_setup(tmp_path.resolve(), RECOVERY_BUDGET)

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
                    f"Create {NOTE_FILE} in this room's workspace containing the "
                    f"marker {marker}. {_store_completion_memory(marker)} "
                    "Include the marker in your reply.",
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, deadline_s=RECOVERY_BUDGET.deadline_s
                )
                replies.assert_contains_any([marker])
                assert (workspaces[room_id] / NOTE_FILE).read_text().strip() == marker
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
            assert (workspaces[room_id] / NOTE_FILE).read_text().strip() == marker
