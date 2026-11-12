"""Kit for the live Cursor ACP workflow smokes: budgets, the sample project, and
turn checkpoints over an approval room."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn, TypeVar

import pytest
from band_rest import ChatMessage

from band.converters.acp_client import ACP_CLIENT_RESUME_RECORD
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.integrations.acp.cursor import is_cursor_band_tool
from band.runtime.tools.types import BandTool
from tests.e2e.baseline.agents import Adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvalroom import (
    TERMINAL_POLL_INTERVAL_S,
    ApprovalRoom,
)
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    Outcome,
)
from tests.e2e.baseline.timeouts import SlowTurnBudget, slow_turn_budget
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations.memories import Memories
from tests.e2e.baseline.toolkit.observations.tool_calls import MemoryTool
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

TURN_BUDGET_S = BaselineSettings().e2e_timeout
WORKFLOW_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=6)
RECOVERY_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=4)
PROJECT_TEST_TIMEOUT_S = 30
SOURCE_FILE = "calculator.py"
TEST_FILE = "test_calculator.py"
BACKUP_FILE = "backup.txt"
NOTE_FILE = "note.txt"
# Operands and expected sum once calculator.py is repaired.
REPAIRED_TOTAL = (2, 3, 5)
PROJECT_WRITE_PROMPT = "Use a shell tool for project writes. Keep replies short."
T = TypeVar("T")


@dataclass(frozen=True)
class TurnCheckpoint:
    cursor: int
    # Server time of the room's newest item before the turn; None in an empty room.
    started: datetime | None


async def room_now(user_ops: UserOps, room_id: str) -> datetime | None:
    latest = await user_ops.list_messages(room_id, limit=1)
    return latest[-1].inserted_at if latest else None


def posted_after(message: ChatMessage, stamp: datetime | None) -> bool:
    # Strict, so the previous turn's close event at the boundary never counts.
    return stamp is None or (
        message.inserted_at is not None and message.inserted_at > stamp
    )


async def start_turn(room: ApprovalRoom, text: str) -> TurnCheckpoint:
    started = await room_now(room.user_ops, room.room_id)
    return TurnCheckpoint(await room.say(text), started)


async def turn_failure(
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


async def within_turn(
    room: ApprovalRoom, checkpoint: TurnCheckpoint, what: str, wait: Awaitable[T]
) -> T:
    try:
        async with asyncio.timeout(room.budget.deadline_s):
            return await wait
    except TimeoutError:
        await turn_failure(room, checkpoint, what)


async def turn_closed(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> bool:
    # A decision releases the triggering message before the detached turn ends,
    # so only the resume record a clean turn posts marks the turn's end.
    tasks = await room.capture.tasks(sender_id=room.agent.id, since=checkpoint.started)
    return any(
        task.content == ACP_CLIENT_RESUME_RECORD.label
        and posted_after(task, checkpoint.started)
        for task in tasks
    )


async def until_closed(room: ApprovalRoom, checkpoint: TurnCheckpoint) -> None:
    while not await turn_closed(room, checkpoint):
        await asyncio.sleep(TERMINAL_POLL_INTERVAL_S)


async def next_request_or_close(
    room: ApprovalRoom, checkpoint: TurnCheckpoint
) -> re.Match[str] | None:
    while True:
        if pending := room.unanswered_requests(since=checkpoint.cursor):
            return pending[0]
        if await turn_closed(room, checkpoint):
            # A permission can land while we were checking for close.
            if pending := room.unanswered_requests(since=checkpoint.cursor):
                return pending[0]
            return None
        await asyncio.sleep(TERMINAL_POLL_INTERVAL_S)


def cursor_room(
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


def agent_setup(
    workdir: Path, budget: SlowTurnBudget, tmp_path_factory: pytest.TempPathFactory
) -> AgentSetup:
    # A pending decision outlives the test's wait, so the test's own failure fires first.
    return AgentSetup(
        workdir,
        budget.deadline_s * 2,
        native_home=tmp_path_factory.mktemp("cursor-home"),
    )


def project(root: Path) -> Path:
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


def store_completion_memory(marker: str) -> str:
    return (
        f"After the work succeeds, call {MemoryTool.STORE.value} exactly once with "
        f"content including {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}, segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.AGENT.value}."
    )


async def stored_memories(capture: ReplyCapture, agent: ProvisionedAgent) -> Memories:
    return (await capture.memory(agent)).stored


def project_state(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    }


def assert_project_unchanged(root: Path, original_state: dict[str, bytes]) -> None:
    assert project_state(root) == original_state


def assert_changed_within(
    root: Path, original_state: dict[str, bytes], allowed: set[str]
) -> None:
    final_state = project_state(root)
    changed = {
        path
        for path in original_state.keys() | final_state.keys()
        if original_state.get(path) != final_state.get(path)
    }
    assert changed <= allowed, changed


async def run_project_command(root: Path, *args: str) -> tuple[int, str]:
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


async def project_tests(root: Path) -> tuple[int, str]:
    return await run_project_command(root, "-m", "unittest", "discover")


async def assert_repaired_project(root: Path) -> None:
    left, right, expected = REPAIRED_TOTAL
    result, output = await run_project_command(
        root,
        "-c",
        f"from {Path(SOURCE_FILE).stem} import total; "
        f"actual = total({left}, {right}); "
        f"assert actual == {expected}, f'{{actual}} != {expected}'",
    )
    assert result == 0, output
    result, output = await project_tests(root)
    assert result == 0, output


async def permission_request(
    room: ApprovalRoom, checkpoint: TurnCheckpoint
) -> re.Match[str]:
    [request] = await within_turn(
        room,
        checkpoint,
        "Cursor did not request permission",
        room.requests(1, since=checkpoint.cursor),
    )
    return request


async def decide_and_confirm(
    room: ApprovalRoom,
    request: re.Match[str],
    outcome: Outcome,
    checkpoint: TurnCheckpoint,
) -> None:
    """Decide one native call and wait for Cursor to confirm the decision."""
    assert not is_cursor_band_tool(request["tool"], tuple(BandTool)), (
        f"Cursor asked the room to approve a Band tool: {request['tool']}"
    )
    after_decision = await room.decide(outcome, request)
    await within_turn(
        room,
        checkpoint,
        f"Cursor did not confirm the {outcome} decision",
        room.shown(room.dialect.notice(outcome, request).text, since=after_decision),
    )


async def decide_permission_and_expect_close(
    room: ApprovalRoom,
    request: re.Match[str],
    *,
    checkpoint: TurnCheckpoint,
    reply_marker: str,
    deny_first_tool: str | None = None,
) -> None:
    """Decide the requested native call, decline any further one, and expect
    the turn's reply; further calls are the model's choice, not the gate's."""
    outcome = Outcome.APPROVE
    if deny_first_tool is not None:
        assert deny_first_tool in request["tool"], (
            f"Cursor did not request the expected project action: {request['tool']}"
        )
        outcome = Outcome.DECLINE
    await decide_and_confirm(room, request, outcome, checkpoint)
    while pending := await within_turn(
        room,
        checkpoint,
        f"Cursor did not finish after {outcome}",
        next_request_or_close(room, checkpoint),
    ):
        await decide_and_confirm(room, pending, Outcome.DECLINE, checkpoint)
    await within_turn(
        room,
        checkpoint,
        "Cursor closed the turn without its reply",
        room.shown(reply_marker, since=checkpoint.cursor),
    )
