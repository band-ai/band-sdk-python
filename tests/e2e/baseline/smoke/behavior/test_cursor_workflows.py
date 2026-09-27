"""Live Cursor ACP work across human decisions, project edits, and room restart."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
from acp.schema import SessionConfigOptionSelect

from band.adapters.cursor_acp import (
    DECISION_RESOLVED_TEMPLATE,
    ROOM_COMMAND,
    CursorACPAdapter,
    CursorCommandWord,
)
from band.client.streaming import DeliveryStatus
from band.core.memory_types import (
    MemoryListScope,
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.types import Capability
from band.integrations.acp.session_config import (
    ACPConfigRequest,
    SessionConfigResolver,
    flatten_select_options,
)
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
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

REPAIR_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
PLAN_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
RECOVERY_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=4)
CURSOR_MODE_OPTION_ID = "mode"
MAX_PLAN_REQUESTS = 3
MAX_PERMISSION_REQUESTS = 8
PLAN_REQUEST = re.compile(
    rf"{re.escape(ROOM_COMMAND)} {CursorCommandWord.ACCEPT} (?P<token>[\w-]+)"
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
        "After the work succeeds, call band_store_memory exactly once with "
        f"content including {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}, segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.AGENT.value}."
    )


async def _stored_count(
    capture: ReplyCapture, agent: ProvisionedAgent, marker: str
) -> int:
    observation = await capture.memory(
        agent, scope=MemoryListScope.AGENT, content_query=marker
    )
    return len(observation.stored.where(content=marker))


async def _project_tests(root: Path) -> int:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "unittest",
        "discover",
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    assert process.returncode is not None
    return process.returncode


def _select_mode(mode: str) -> SessionConfigResolver:
    async def resolve(request: ACPConfigRequest) -> dict[str, str]:
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


async def _plan_token(room: ApprovalRoom, *, since: int) -> str:
    def find_token() -> str | None:
        for message in room.capture.messages.since(since):
            if "needs approval" in (message.content or "") and (
                match := PLAN_REQUEST.search(message.content or "")
            ):
                return match["token"]
        return None

    try:
        await room.capture.wait_until(
            lambda _messages: find_token() is not None,
            deadline_s=room.budget.deadline_s,
        )
    except TimeoutError:
        pytest.fail(
            f"Cursor did not send cursor/create_plan in plan mode; "
            f"room messages: {room.said_since(since)}"
        )
    token = find_token()
    assert token is not None
    return token


async def _decide_plan(
    room: ApprovalRoom, *, since: int, accept: bool, reply_marker: str
) -> None:
    token = await _plan_token(room, since=since)
    word = CursorCommandWord.ACCEPT if accept else CursorCommandWord.REJECT
    for _ in range(MAX_PLAN_REQUESTS):
        after_decision = await room.say(f"{ROOM_COMMAND} {word} {token}")
        notice = DECISION_RESOLVED_TEMPLATE.format(kind="plan", token=token)
        await room.shown(notice, since=after_decision)
        try:
            messages = await room.capture.wait_until(
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
        next_token = next(
            (
                match["token"]
                for item in messages[after_decision:]
                if (match := PLAN_REQUEST.search(item.content or ""))
            ),
            None,
        )
        if next_token is None:
            return
        token = next_token
    pytest.fail(
        f"Cursor requested more than {MAX_PLAN_REQUESTS} plans: "
        f"{room.said_since(since)}"
    )


async def _decide_permissions_until_reply(
    room: ApprovalRoom,
    *,
    since: int,
    reply_marker: str,
    deny_tool: str | None = None,
) -> int:
    [request] = await room.requests(1, since=since)
    denied = 0
    for _ in range(MAX_PERMISSION_REQUESTS):
        outcome = (
            Outcome.DECLINE
            if deny_tool is not None and deny_tool in request["tool"]
            else Outcome.APPROVE
        )
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
            return denied
        request = requests[0]
    pytest.fail(
        f"Cursor requested permission more than {MAX_PERMISSION_REQUESTS} times: "
        f"{room.said_since(since)}"
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
    assert await _project_tests(root) != 0
    original = source.read_text()
    backup = root / "backup.txt"
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
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            REPAIR_BUDGET,
        )
        start = await room.say(
            "Inspect the failing Python project. Before repairing it, use one "
            "shell command to copy calculator.py to backup.txt. Do not edit "
            "the project until that command is decided. Only after the copy "
            f"succeeds, {_store_completion_memory(marker)} If I "
            f"deny the copy, do not retry; reply with {denied_reply}."
        )
        denied = await _decide_permissions_until_reply(
            room,
            since=start,
            reply_marker=denied_reply,
            deny_tool=backup.name,
        )
        assert denied >= 1
        assert source.read_text() == original
        assert not backup.exists()
        assert await _stored_count(capture, agent, marker) == 0

        start = await room.say(
            "Skip the backup. Diagnose and repair calculator.py so the "
            "existing unittest passes. Use one shell tool call for the edit "
            "and test run. "
            f"{_store_completion_memory(marker)} Then report the result "
            f"with {report}."
        )
        assert source.read_text() == original
        await _decide_permissions_until_reply(
            room,
            since=start,
            reply_marker=report,
        )
        assert source.read_text() != original
        assert await _project_tests(root) == 0
        assert await _stored_count(capture, agent, marker) == 1


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
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            PLAN_BUDGET,
        )
        start = await room.say(
            "Read calculator.py and test_calculator.py in this workspace. "
            "Plan a multi-step repair of the failing calculator test. Submit "
            "the plan for human review with cursor/create_plan, not just a "
            "chat outline. Do not implement yet. If I reject it, do not "
            f"request another plan; reply with {rejected_reply}."
        )
        await _decide_plan(room, since=start, accept=False, reply_marker=rejected_reply)
        assert source.read_text() == original

    async with (
        running_agent(identity, plan_adapter(), cell.settings),
        reply_capture(room_id) as capture,
    ):
        room = ApprovalRoom(
            identity,
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            PLAN_BUDGET,
        )
        start = await room.say(
            "Revise the plan to keep the change limited to calculator.py and "
            "its test, then request human review with cursor/create_plan again. "
            f"If I accept it, reply with {accepted_reply}. Do not implement yet."
        )
        await _decide_plan(room, since=start, accept=True, reply_marker=accepted_reply)
        assert source.read_text() == original

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
            room_id,
            capture,
            DIALECTS[Adapter.CURSOR_ACP],
            user_ops,
            PLAN_BUDGET,
        )
        start = await room.say(
            "Implement the accepted calculator repair, run its unittest in "
            "the same shell tool call, and report the result with "
            f"{implementation_reply}."
        )
        assert source.read_text() == original
        await _decide_permissions_until_reply(
            room,
            since=start,
            reply_marker=implementation_reply,
        )
    assert source.read_text() != original
    assert await _project_tests(root) == 0


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
        for room_id in rooms:
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
                assert await _stored_count(capture, identity, marker) == 1
                handled[room_id] = mid

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
        for room_id in rooms:
            assert DeliveryStatus.PROCESSING not in captures[room_id].delivery_history(
                handled[room_id], identity.id
            )
            marker = markers[room_id]
            assert await _stored_count(captures[room_id], identity, marker) == 1
            assert (workspaces[room_id] / "note.txt").read_text().strip() == marker
