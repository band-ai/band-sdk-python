"""Isolated Copilot SDK live behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
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
from tests.e2e.baseline.toolkit.builders import copilot_home_dir
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.skipif(
    not _COPILOT_SDK_AVAILABLE,
    reason="github-copilot-sdk not installed (pip install band-sdk[copilot_sdk])",
)


class TestSharedClient:
    @lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
    @requires(Dep.ANTHROPIC)
    @flaky_infra(
        "several live turns across two adapter lifecycles can time out transiently"
    )
    # Two sequential adapter lifecycles on one shared runtime, the first serving
    # two concurrent room sessions — several live turns, so widen the outer budget.
    @pytest.mark.timeout(extra=180)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_copilot_shared_client_across_adapter_lifecycles(
        self,
        copilot_adapter: Callable[..., CopilotSDKAdapter],
        baseline_settings: BaselineSettings,
        tmp_path: Path,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        """Proves the Copilot SDK's one-client/many-sessions model through the
        adapter against the live platform:

        1. one ``CopilotClient`` is created by the test (the owner);
        2. an adapter *borrows* it and serves TWO rooms with turns in flight
           CONCURRENTLY — two isolated parallel sessions on one runtime;
        3. the first adapter shuts down and a successor adapter reuses the same
           still-running client — the borrowed client must survive an adapter's
           full cleanup (``owns_client=False`` contract).
        """
        from copilot import (  # noqa: PLC0415 -- copilot_sdk extra; file collects even when absent, skipped via _COPILOT_SDK_AVAILABLE at test time
            CopilotClient,
        )

        identity = await resource_manager.provision_agent("copilot-shared-client")
        room_a = await resource_manager.provision_room(
            title="e2e-copilot-shared-a", participants=[identity.id]
        )
        room_b = await resource_manager.provision_room(
            title="e2e-copilot-shared-b", participants=[identity.id]
        )

        async def smoke(room_id: str, *, prompt: str = "Please say hello.") -> None:
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    prompt,
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, deadline_s=baseline_settings.e2e_timeout
                )
            replies.assert_present(what="a copilot_sdk[shared-client] reply")

        def make_shared_adapter(client: Any) -> CopilotSDKAdapter:
            return copilot_adapter(client=client)

        # The test owns the client; adapters only borrow it.
        client = CopilotClient(
            use_logged_in_user=False, base_directory=copilot_home_dir(str(tmp_path))
        )
        try:
            async with running_agent(
                identity, make_shared_adapter(client), baseline_settings
            ):
                # Two rooms with turns in flight AT THE SAME TIME — parallel
                # sessions on one runtime, serialized only per room.
                await asyncio.gather(smoke(room_a), smoke(room_b))

            # First adapter fully cleaned up; the borrowed client must have
            # survived — a successor adapter reuses it without a restart.
            async with running_agent(
                identity, make_shared_adapter(client), baseline_settings
            ):
                # A new request: the resumed session already answered the first,
                # and a repeat of it may rightly be declined with band_no_reply.
                await smoke(room_a, prompt="Please say goodbye.")
        finally:
            await client.stop()
