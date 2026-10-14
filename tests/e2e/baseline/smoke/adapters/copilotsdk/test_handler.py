"""Isolated Copilot SDK live behavior."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from band.adapters.copilot_sdk import (
    _COPILOT_SDK_AVAILABLE,
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


class TestAskUserHandler:
    @lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
    @requires(Dep.ANTHROPIC)
    @flaky_infra(
        "Copilot runtime boot + ask_user double round-trip can time out transiently"
    )
    # ask_user adds a second model round trip (ask -> handler answers -> final
    # reply) on top of Copilot's own runtime boot.
    @pytest.mark.timeout(extra=120)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_copilot_ask_user_handler_round_trips_to_room_reply(
        self,
        copilot_adapter: Callable[..., CopilotSDKAdapter],
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        """``ask_user=<handler>`` pauses the turn, a programmatic operator answers.

        The operator's answer is a high-entropy token the user never utters, so the
        reply containing it proves the answer flowed through the handler — not
        model invention. Guards the adapter's ``ask_user`` allowlisting and handler
        forwarding (without either, the model cannot ask and the handler never
        fires).
        """

        operator_channel = unique_marker("channel")
        asked: list[dict[str, Any]] = []

        async def fake_operator(
            request: dict[str, Any], context: dict[str, str]
        ) -> dict[str, Any]:
            asked.append(dict(request))
            return {"answer": operator_channel, "wasFreeform": True}

        adapter = copilot_adapter(
            ask_user=fake_operator,
            custom_section=(
                "Keep responses short and concise. A human operator is "
                "available through the ask_user tool for decisions you "
                "cannot make alone."
            ),
        )

        async with running_provisioned_agent(
            adapter, resource_manager, label="copilot-ask-user"
        ) as agent:
            room_id = await resource_manager.provision_room(
                title="e2e-copilot-ask-user", participants=[agent.id]
            )
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    "I need to deploy release v2. Use the ask_user tool to ask "
                    "your operator which channel to deploy to and wait for my "
                    "response before continuing. Then reply stating exactly "
                    "the channel the operator chose.",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await capture.wait_for_reply(
                    mid, agent.id, deadline_s=baseline_settings.e2e_timeout * 2
                )

        assert asked, "the model never called ask_user (handler did not fire)"
        assert asked[0].get("question"), f"ask_user carried no question: {asked[0]!r}"
        # Only the operator handler can supply this token; the reply containing it
        # proves the answer round-tripped through the turn.
        replies.assert_contains_any([operator_channel])
