"""Private native sessions retain their identities across adapter restarts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.antigravity.sessions import (
    assert_private_homes,
    room_session_id,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    RECALL,
    REMEMBER,
    REPLY_PROMPT,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    from band.adapters.antigravity_acp import AntigravityACPAdapter

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=9)


class TestAntigravityNativeSessions:
    @per_adapter(Adapter.ANTIGRAVITY_ACP, prompt=REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_native_sessions_resume_per_agent_and_room(
        self,
        cell: AdapterCell,
        persistent_antigravity: Callable[[], AntigravityACPAdapter],
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
    ) -> None:
        first = await cell.provision(label="antigravity-first")
        second = await cell.provision(label="antigravity-second")
        shared = await cell.resources.provision_room(participants=[first.id, second.id])
        private = await cell.resources.provision_room(participants=[first.id])
        sessions = ((first, shared), (first, private), (second, shared))
        notes = {
            (identity.id, room): unique_marker("native") for identity, room in sessions
        }
        other_room_notes = {
            (first.id, shared): [notes[first.id, private]],
            (first.id, private): [notes[first.id, shared]],
            (second.id, shared): [],
        }
        native_ids: dict[tuple[str, str], str] = {}
        task_ids: dict[tuple[str, str], list[str]] = {}

        with (
            cell.resources.track_running(first.id),
            cell.resources.track_running(second.id),
        ):
            async with (
                running_agent(first, persistent_antigravity(), cell.settings),
                running_agent(second, persistent_antigravity(), cell.settings),
            ):
                for identity, room in sessions:
                    key = (identity.id, room)
                    async with reply_capture(room) as capture:
                        mid = await user_ops.send_message(
                            room,
                            REMEMBER.format(note=notes[key]),
                            mention_id=identity.id,
                            mention_name=identity.name,
                        )
                        await capture.wait_for_processed(
                            mid, identity.id, deadline_s=BUDGET.deadline_s
                        )
                        tasks = await capture.tasks(sender_id=identity.id)
                        native_ids[key] = room_session_id(tasks, room)
                        task_ids[key] = [task.id for task in tasks]

                assert_private_homes(
                    [
                        tmp_path / "native" / identity.id / room
                        for identity, room in sessions
                    ]
                )

            assert len(set(native_ids.values())) == len(sessions)

            async with (
                running_agent(first, persistent_antigravity(), cell.settings),
                running_agent(second, persistent_antigravity(), cell.settings),
            ):
                for identity, room in sessions:
                    key = (identity.id, room)
                    async with reply_capture(room) as capture:
                        mid = await user_ops.send_message(
                            room,
                            RECALL,
                            mention_id=identity.id,
                            mention_name=identity.name,
                        )
                        replies = await capture.wait_for_reply(
                            mid, identity.id, deadline_s=BUDGET.deadline_s
                        )
                        replies.assert_contains_any([notes[key]])
                        replies.assert_contains_none(other_room_notes[key])
                        await capture.wait_for_processed(
                            mid, identity.id, deadline_s=BUDGET.deadline_s
                        )
                        tasks = await capture.tasks(sender_id=identity.id)
                        fresh = tasks.excluding(*task_ids[key])
                        assert room_session_id(fresh, room) == native_ids[key]
