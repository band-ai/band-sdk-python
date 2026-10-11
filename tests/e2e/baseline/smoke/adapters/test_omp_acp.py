"""Live smoke coverage for OMP over ACP."""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING

import pytest

from band.core.types import MessageType
from band.integrations.acp.types import ChunkType
from band.integrations.omp import (
    OMP_APPROVAL_FORM_TOOL_NAME,
    finalize_omp_command,
    omp_command_in_workspace,
)
from tests.e2e.baseline.agents import Adapter, Lane, lane, with_adapters
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
from tests.e2e.baseline.toolkit.omp_credentials import omp_command, omp_model
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.paths import REPO_ROOT

if TYPE_CHECKING:
    from band.adapters.omp_acp import OmpACPAdapter

FORM_PROBE_TITLE = "Approve the E2E form probe?"
BAND_EVENT_TOOL_NAME = "band_send_event"
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=1)


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_raw_acp_prompt_completes(
    baseline_settings: BaselineSettings,
) -> None:
    """Distinguish an OMP/provider stall from a Band delivery failure."""
    from band.integrations.acp.client_runtime import ACPRuntime  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="band-e2e-omp-raw-") as sandbox:
        command = finalize_omp_command(
            omp_command(baseline_settings), model=omp_model(baseline_settings)
        )
        runtime = ACPRuntime(
            command=omp_command_in_workspace(command, sandbox),
            env=omp_acp_env(baseline_settings, omp_agent_home_dir(sandbox)),
            use_unstable_protocol=True,
        )
        stage = "initialize"
        try:
            async with asyncio.timeout(BUDGET.deadline_s):
                await runtime.start()
                stage = "new_session"
                session_id = await runtime.create_session(cwd=sandbox, mcp_servers=[])
                stage = "prompt"
                chunks = await runtime.prompt(
                    session_id=session_id,
                    prompt_text="Reply with one short greeting.",
                )
        except TimeoutError:
            pytest.fail(f"OMP raw ACP {stage} did not settle before the turn deadline")
        finally:
            await runtime.stop()

    assert any(
        chunk.chunk_type == ChunkType.TEXT and chunk.content.strip() for chunk in chunks
    ), "OMP raw ACP prompt completed without a text response"


@with_adapters(Adapter.OMP_ACP, **TOOL_AGENT)
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("ompevent")
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


def _probe_denying_omp_adapter(
    settings: BaselineSettings, sandbox: str
) -> OmpACPAdapter:
    """OMP denies the native probe while permitting Band completion."""
    # Deferred: omp_acp pulls in the optional `acp` package, not installed in
    # every lane's venv (e.g. dev-crewai, dev-parlant) -- importing at module
    # level would break collection there.
    from band.adapters.omp_acp import (  # noqa: PLC0415
        OmpACPAdapter,
        OmpACPAdapterConfig,
    )
    from tests.e2e.baseline.smoke.samples.ompapproval import (  # noqa: PLC0415
        deny_probe_allow_completion,
    )

    return OmpACPAdapter(
        config=OmpACPAdapterConfig(
            command=(
                *omp_command(settings),
                f"--extension={REPO_ROOT / 'tests/e2e/baseline/fixtures/omp-form-probe.js'}",
            ),
            model=omp_model(settings),
            custom_section="Keep replies short.",
            cwd=sandbox,
            env=omp_acp_env(settings, omp_agent_home_dir(sandbox)),
        ),
        resolve_permission=deny_probe_allow_completion,
    )


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_form_elicitation_denied_is_narrated(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A native OMP form is denied and narrated through the real ACP boundary.

    The extension asks before the model turn, so form coverage does not depend
    on which file tool a model happens to choose. The turn must then deliver
    its requested reply through a Band completion tool.
    """
    with tempfile.TemporaryDirectory(prefix="band-e2e-omp-acp-deny-") as sandbox:
        adapter = _probe_denying_omp_adapter(baseline_settings, sandbox)
        async with running_provisioned_agent(
            adapter, resource_manager, label="omp-acp-form-deny"
        ) as agent:
            room_id = await resource_manager.provision_room(
                title="e2e-omp-acp-form-deny", participants=[agent.id]
            )
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    "Respond briefly after handling the form probe.",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await capture.wait_for_reply(
                    mid, agent.id, deadline_s=BUDGET.deadline_s
                )
                tool_calls = await capture.tool_calls(sender_id=agent.id)
                tool_results = await capture.tool_results(sender_id=agent.id)

            replies.assert_present()
            probe_calls = [
                call
                for call in tool_calls.named(OMP_APPROVAL_FORM_TOOL_NAME)
                if call.args.get("message") == FORM_PROBE_TITLE
            ]
            assert len(probe_calls) == 1, "expected exactly one native form probe"
            probe_call = probe_calls[0]
            assert probe_call.tool_call_id, "native form probe must have a call ID"
            probe_results = [
                result
                for result in tool_results.named(OMP_APPROVAL_FORM_TOOL_NAME)
                if result.tool_call_id == probe_call.tool_call_id
            ]
            assert len(probe_results) == 1, "expected the native probe's result"
            result = probe_results[0]
            assert result.is_error and "cancelled" in result.output.lower()
            assert result.raw.metadata is not None
            assert result.raw.metadata.model_dump()["permission_outcome"] == "cancelled"
