"""Deny the native probe without denying Band completion."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.omp.kit import (
    BUDGET,
    denying_omp_adapter,
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
        from tests.e2e.baseline.smoke.samples.omppermission import (  # noqa: PLC0415 -- this sample imports the optional native adapter
            FORM_PROBE_TITLE,
        )

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
                assert [
                    call
                    for call in tool_calls
                    if call.args.get("message") == FORM_PROBE_TITLE
                ] == []
                assert (
                    sum(request.title == FORM_PROBE_TITLE for request in requests) == 1
                )
                assert all(
                    request.room_id == room_id
                    and request.session_id
                    and request.options == (OMP_FORM_APPROVE, OMP_FORM_DENY)
                    for request in requests
                )
                assert (
                    Path(sandbox) / "permission-result"
                ).read_text() == OMP_FORM_DENY
