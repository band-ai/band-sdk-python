"""A human gate decides Cursor's native project work while Band tools never ask."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.core.types import Capability
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.behavior.cursorworkflows.kit import (
    BACKUP_FILE,
    PROJECT_WRITE_PROMPT,
    SOURCE_FILE,
    TEST_FILE,
    WORKFLOW_BUDGET,
    agent_setup,
    assert_changed_within,
    assert_project_unchanged,
    assert_repaired_project,
    cursor_room,
    decide_permission_and_expect_close,
    permission_request,
    project,
    project_state,
    project_tests,
    start_turn,
    store_completion_memory,
    stored_memories,
)
from tests.e2e.baseline.smoke.samples.approvals import cursor_test_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestCursorHumanGate:
    @per_adapter(Adapter.CURSOR_ACP)
    @pytest.mark.timeout(extra=WORKFLOW_BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_repairs_a_failing_project_after_a_human_gate(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        root = tmp_path.resolve()
        source = project(root)
        initial_exit, initial_output = await project_tests(root)
        assert initial_exit != 0, initial_output
        original = source.read_text()
        original_state = project_state(root)
        marker = unique_marker("repairevent")
        report = unique_marker("repairreport")
        denied_reply = unique_marker("repairdenied")
        setup = agent_setup(root, WORKFLOW_BUDGET, tmp_path_factory)
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
            room = cursor_room(agent, room_id, capture, user_ops, WORKFLOW_BUDGET)
            checkpoint = await start_turn(
                room,
                "Before reading or changing any project file, use one shell command "
                f"to copy {SOURCE_FILE} to {BACKUP_FILE}. Do not edit the project until "
                "that command is decided. Only after the copy "
                f"succeeds, {store_completion_memory(marker)} If I "
                f"deny the copy, do not retry; reply with {denied_reply}.",
            )
            request = await permission_request(room, checkpoint)
            await decide_permission_and_expect_close(
                room,
                request,
                checkpoint=checkpoint,
                reply_marker=denied_reply,
                deny_first_tool=BACKUP_FILE,
            )
            assert_project_unchanged(root, original_state)
            assert len(await stored_memories(capture, agent)) == 0

            checkpoint = await start_turn(
                room,
                f"Skip the backup. Diagnose and repair {SOURCE_FILE} so the "
                "existing unittest passes. Use one shell tool call for the edit "
                "and test run. "
                f"{store_completion_memory(marker)} Then report the result "
                f"with {report}.",
            )
            request = await permission_request(room, checkpoint)
            # Nothing may change while the human gate is pending.
            assert_project_unchanged(root, original_state)
            await decide_permission_and_expect_close(
                room, request, checkpoint=checkpoint, reply_marker=report
            )
            assert source.read_text() != original
            assert (root / TEST_FILE).read_bytes() == original_state[TEST_FILE]
            assert_changed_within(root, original_state, {SOURCE_FILE})
            await assert_repaired_project(root)
            stored = await stored_memories(capture, agent)
            assert len(stored) == 1
            stored.assert_stored(content=marker)
