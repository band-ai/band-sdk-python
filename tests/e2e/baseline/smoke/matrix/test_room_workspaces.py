"""Real relative files stay isolated between rooms and survive agent restart."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.workspaces import create_room_workspace_resolver
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    read_workspace_instruction,
    unique_marker,
    write_workspace_instruction,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

WORKSPACE_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=4)


@per_adapter(
    Adapter.CLAUDE_SDK,
    Adapter.CODEX,
    Adapter.COPILOT_ACP,
    Adapter.CURSOR_ACP,
    Adapter.OMP_ACP,
    prompt=REPLY_PROMPT,
)
@pytest.mark.timeout(extra=WORKSPACE_BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_room_files_survive_restart_without_cross_leak(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Path,
) -> None:
    identity = await cell.provision()
    rooms = [
        await cell.resources.provision_room(participants=[identity.id])
        for _ in range(2)
    ]
    resolver = create_room_workspace_resolver(tmp_path)
    filename = "room-note.txt"
    markers = [unique_marker("alpha"), unique_marker("bravo")]
    adapter = cell.build(workspace_for_room=resolver)
    with cell.resources.track_running(identity.id):
        async with running_agent(identity, adapter, cell.settings):
            for room_id, marker in zip(rooms, markers, strict=True):
                async with reply_capture(room_id) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        write_workspace_instruction(filename, marker),
                        mention_id=identity.id,
                        mention_name=identity.name,
                    )
                    await capture.wait_for_reply(
                        mid, identity.id, deadline_s=WORKSPACE_BUDGET.deadline_s
                    )
                    assert (Path(resolver(room_id)) / filename).read_text() == marker

    # These new values never enter chat history; only reading disk can recover them.
    markers = [unique_marker("afteralpha"), unique_marker("afterbravo")]
    for room_id, marker in zip(rooms, markers, strict=True):
        (Path(resolver(room_id)) / filename).write_text(marker)

    adapter = cell.build(workspace_for_room=resolver)
    with cell.resources.track_running(identity.id):
        async with running_agent(identity, adapter, cell.settings):
            for index, room_id in enumerate(rooms):
                async with reply_capture(room_id) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        read_workspace_instruction(filename),
                        mention_id=identity.id,
                        mention_name=identity.name,
                    )
                    replies = await capture.wait_for_reply(
                        mid, identity.id, deadline_s=WORKSPACE_BUDGET.deadline_s
                    )
                    replies.assert_contains_any([markers[index]])
                    replies.assert_contains_none([markers[1 - index]])
