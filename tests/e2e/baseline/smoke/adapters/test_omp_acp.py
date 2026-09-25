"""Live smoke coverage for OMP over ACP."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.core.types import MessageType
from band.integrations.omp import OMP_APPROVAL_FORM_TOOL_NAME
from tests.e2e.baseline.agents import Adapter, Lane, lane, with_adapters
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.builders import omp_acp_env, omp_agent_home_dir
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    from band.adapters.omp_acp import OmpACPAdapter
    from band.integrations.acp.client_adapter import ACPPermissionRequest

BAND_EVENT_TOOL_NAME = "band_send_event"
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=1)


@with_adapters(Adapter.OMP_ACP, **TOOL_AGENT)
@flaky_model("OMP may occasionally miss an explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("omp-acp-event")
    room_id = await resource_manager.provision_room(
        title="e2e-omp-acp-tool-call", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_call_events = await capture.events(
            MessageType.TOOL_CALL, sender_id=agent.id
        )

    thoughts.assert_contains_any([marker])
    tool_call_events.assert_at_least(1)
    tool_call_events.assert_contains_any([BAND_EVENT_TOOL_NAME])


def _denying_omp_adapter(settings: BaselineSettings) -> OmpACPAdapter:
    """OMP with always-ask plus a PermissionResolver that denies every ask."""
    # Deferred: omp_acp pulls in the optional `acp` package, not installed in
    # every lane's venv (e.g. dev-crewai, dev-parlant) -- importing at module
    # level would break collection there.
    from band.adapters.omp_acp import (  # noqa: PLC0415
        OmpACPAdapter,
        OmpACPAdapterConfig,
    )

    async def deny(_request: ACPPermissionRequest) -> None:
        return None

    sandbox = tempfile.mkdtemp(prefix="band-e2e-omp-acp-deny-")
    return OmpACPAdapter(
        config=OmpACPAdapterConfig(
            custom_section=(
                "You are a coding agent. When asked to delete or overwrite a file, "
                "you must attempt the destructive tool action rather than refusing "
                "in plain text. Keep replies short."
            ),
            cwd=sandbox,
            env=omp_acp_env(settings, omp_agent_home_dir(sandbox)),
            resolve_permission=deny,
        )
    )


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@flaky_model("OMP form elicitation depends on the model attempting a gated action")
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_form_elicitation_denied_is_narrated(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A destructive ask that OMP surfaces as a form is denied and narrated.

    always-ask + a denying PermissionResolver turns the form into a cancelled
    synthetic permission pair (``omp_approval_form``). The resumed model reply
    is not asserted — only that denial narration reached the room.
    """
    adapter = _denying_omp_adapter(baseline_settings)
    target_path = Path(tempfile.mkstemp(prefix="band-omp-deny-", suffix=".txt")[1])
    target_path.write_text("keep-me", encoding="utf-8")

    async with running_provisioned_agent(
        adapter, resource_manager, label="omp-acp-form-deny"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-omp-acp-form-deny", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                f"Delete the file at `{target_path}` using your file tools. "
                "You must attempt the delete; do not only describe it.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(
                mid, agent.id, deadline_s=BUDGET.deadline_s
            )
            tool_calls = await capture.events(MessageType.TOOL_CALL, sender_id=agent.id)
            tool_results = await capture.tool_results(sender_id=agent.id)

        tool_calls.assert_contains_any([OMP_APPROVAL_FORM_TOOL_NAME])
        form_results = tool_results.named(OMP_APPROVAL_FORM_TOOL_NAME)
        form_results.assert_present()
        assert any(
            result.is_error and "cancelled" in result.output.lower()
            for result in form_results
        )
        assert any(
            (
                result.raw.metadata.model_dump()
                if result.raw.metadata is not None
                else {}
            ).get("permission_outcome")
            == "cancelled"
            for result in form_results
        )
