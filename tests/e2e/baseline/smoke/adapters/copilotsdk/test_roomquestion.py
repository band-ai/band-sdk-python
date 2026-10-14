"""Isolated Copilot SDK live behavior."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from band.adapters.copilot_sdk import (
    _COPILOT_SDK_AVAILABLE,
    ASK_USER_ROOM,
    CopilotSDKAdapter,
)
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.skipif(
    not _COPILOT_SDK_AVAILABLE,
    reason="github-copilot-sdk not installed (pip install band-sdk[copilot_sdk])",
)


class TestRoomQuestion:
    @lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
    @requires(Dep.ANTHROPIC)
    @flaky_infra(
        "two full live turns plus Copilot runtime boot can time out transiently"
    )
    @pytest.mark.timeout(extra=180)  # two full turns (question turn + answer turn)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_copilot_ask_user_room_question_answered_by_next_message(
        self,
        copilot_adapter: Callable[..., CopilotSDKAdapter],
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        """``ask_user="room"`` posts the question into the room and ends the turn;
        the user's next room message answers it on the same persisted session.

        The answer is a high-entropy token never uttered before the answer, so a
        reply containing it proves the answer flowed through the room round trip —
        not model invention.
        """

        secret_channel = unique_marker("channel")
        adapter = copilot_adapter(
            ask_user=ASK_USER_ROOM,
            custom_section=(
                "Keep responses short and concise. Never invent answers to "
                "questions you asked — wait for the user."
            ),
        )

        async with running_provisioned_agent(
            adapter, resource_manager, label="copilot-ask-user-room"
        ) as agent:
            room_id = await resource_manager.provision_room(
                title="e2e-copilot-ask-user-room", participants=[agent.id]
            )
            async with reply_capture(room_id) as capture:
                # Turn 1: the trigger makes the model call ask_user; the question
                # must land in the room, not anywhere else.
                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    "Use the ask_user tool to ask me which channel to deploy "
                    "release v2 to. After I answer, reply stating exactly the "
                    "channel I chose.",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await capture.wait_for_reply(
                    mid,
                    agent.id,
                    since=mark,
                    deadline_s=baseline_settings.e2e_timeout * 2,
                )
                replies.assert_contains_any(["channel"])

                # Turn 2: the user's next room message is the answer.
                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    f"Deploy to the {secret_channel} channel.",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await capture.wait_for_reply(
                    mid,
                    agent.id,
                    since=mark,
                    deadline_s=baseline_settings.e2e_timeout * 2,
                )
                # Only the answer message contains this token; the reply relaying
                # it proves the question/answer round-tripped through the room.
                replies.assert_contains_any([secret_channel])
