"""Cursor rooms keep their own work and history across an agent restart."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.client.streaming import DeliveryStatus
from band.core.types import Capability
from band.runtime.tools.types import BandTool
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.behavior.cursorworkflows.kit import (
    NOTE_FILE,
    RECOVERY_BUDGET,
    agent_setup,
    room_now,
    store_completion_memory,
    stored_memories,
)
from tests.e2e.baseline.smoke.samples.approvals import cursor_test_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    from band.adapters.cursor_acp import CursorACPAdapter


class TestCursorRestartRecovery:
    @per_adapter(Adapter.CURSOR_ACP)
    @pytest.mark.timeout(extra=RECOVERY_BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_sequential_rooms_keep_their_work_after_restart(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
        tmp_path_factory: pytest.TempPathFactory,
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
            room: unique_marker(f"room{index}") for index, room in enumerate(rooms)
        }
        setup = replace(
            agent_setup(tmp_path.resolve(), RECOVERY_BUDGET, tmp_path_factory),
            approval="auto_accept",
        )

        def adapter() -> CursorACPAdapter:
            return cursor_test_adapter(
                cell.settings,
                setup,
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
                        f"marker {marker}. {store_completion_memory(marker)} "
                        "Include the marker in your reply.",
                        mention_id=identity.id,
                        mention_name=identity.name,
                    )
                    replies = await capture.wait_for_reply(
                        mid, identity.id, deadline_s=RECOVERY_BUDGET.deadline_s
                    )
                    replies.assert_contains_any([marker])
                    assert (
                        workspaces[room_id] / NOTE_FILE
                    ).read_text().strip() == marker
                    stored = await stored_memories(capture, identity)
                    assert len(stored) == expected_count
                    assert len(stored.where(content=marker)) == 1
                    handled[room_id] = mid

        # Every turn-1 event is durable once its message is processed.
        recall_since = {room_id: await room_now(user_ops, room_id) for room_id in rooms}
        offline = {
            room_id: await user_ops.send_message(
                room_id,
                "Recall the marker from our earlier conversation in this room and "
                f"reply with it using only {BandTool.SEND_MESSAGE}. "
                "Do not read or write files or use memory or other retrieval tools.",
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
            stored = await stored_memories(first, identity)
            assert len(stored) == len(rooms)
            for room_id in rooms:
                calls = await captures[room_id].tool_calls(
                    sender_id=identity.id,
                    since=recall_since[room_id],
                    include_memory=True,
                )
                calls.assert_only(BandTool.SEND_MESSAGE)
                assert DeliveryStatus.PROCESSING not in captures[
                    room_id
                ].delivery_history(handled[room_id], identity.id)
                marker = markers[room_id]
                assert len(stored.where(content=marker)) == 1
                assert (workspaces[room_id] / NOTE_FILE).read_text().strip() == marker
