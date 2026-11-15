"""Deny the native probe without denying Band completion."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from band.integrations.omp import OMP_FORM_DENY
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.omp.kit import (
    BUDGET,
    assert_permission_requests,
    denying_omp_adapter,
    permission_probe_calls,
    permission_probe_file,
    permission_probe_sandbox,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    from band.adapters.omp import OmpPermissionRequest


class TestOmpPermissionDeny:
    @lane(Lane.BACKENDS)
    @requires(Dep.OMP_NATIVE)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_omp_native_permission_is_denied(
        self,
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        requests: list[OmpPermissionRequest] = []
        with permission_probe_sandbox() as sandbox:
            adapter = denying_omp_adapter(
                baseline_settings, sandbox, OMP_FORM_DENY, requests
            )
            async with running_provisioned_agent(
                adapter, resource_manager, label="omp-permission-deny"
            ) as agent:
                room_id = await resource_manager.provision_room(
                    title="e2e-omp-permission-deny", participants=[agent.id]
                )
                async with reply_capture(room_id) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        "Respond briefly after handling the permission probe.",
                        mention_id=agent.id,
                        mention_name=agent.name,
                    )
                    replies = await capture.wait_for_reply(
                        mid, agent.id, deadline_s=BUDGET.deadline_s
                    )
                    tool_calls = await capture.tool_calls(sender_id=agent.id)

                replies.assert_present()
                assert permission_probe_calls(tool_calls) == []
                assert_permission_requests(requests, room_id)
                assert permission_probe_file(sandbox).read_text() == OMP_FORM_DENY
