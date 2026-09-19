"""Live OMP ACP smokes for session reuse, Band MCP, and permission denial."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from band.core.types import MessageType
from band.integrations.acp.client_runtime import select_allow_option_id

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT_SYSTEM_PROMPT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.builders import (
    omp_acp_command,
    omp_acp_env,
    omp_state_dir,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


def omp_config(settings: BaselineSettings, work_dir: Path, **kwargs: Any) -> Any:
    """One hermetic OMP ACP configuration for a live scenario."""
    from band.adapters.omp_acp import OmpACPAdapterConfig  # noqa: PLC0415 -- the ACP extra is optional outside this backend lane

    return OmpACPAdapterConfig(
        command=omp_acp_command(settings),
        cwd=str(work_dir),
        env=omp_acp_env(settings, omp_state_dir(str(work_dir))),
        custom_section=TOOL_AGENT_SYSTEM_PROMPT,
        **kwargs,
    )


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@flaky_model("OMP may miss an explicit Band MCP instruction on a provider retry")
@pytest.mark.timeout(extra=240)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_reuses_a_session_and_narrates_band_mcp(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Path,
) -> None:
    """Two turns reuse one ACP session and a real Band MCP call stays correlated."""
    from band.adapters.omp_acp import OmpACPAdapter  # noqa: PLC0415 -- the ACP extra is optional outside this backend lane

    reply_marker = unique_marker("omp-reply")
    event_marker = unique_marker("omp-event")
    identity = await resource_manager.provision_agent("omp-acp")
    room_id = await resource_manager.provision_room(
        title="e2e-omp-acp", participants=[identity.id]
    )
    adapter = OmpACPAdapter(omp_config(baseline_settings, tmp_path))

    async with running_agent(identity, adapter, baseline_settings):
        async with reply_capture(room_id) as capture:
            first = await user_ops.send_message(
                room_id,
                f"Reply with exactly this marker: {reply_marker}",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(first, identity.id)
            replies.assert_contains_any([reply_marker])

            second = await user_ops.send_message(
                room_id,
                emit_event_instruction(MessageType.THOUGHT, event_marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(second, identity.id)
            thoughts = await capture.thoughts(sender_id=identity.id)
            calls = await capture.tool_calls(sender_id=identity.id)
            results = await capture.tool_results(sender_id=identity.id)
            tasks = await capture.tasks(sender_id=identity.id)

    thoughts.assert_contains_any([event_marker])
    band_calls = calls.named("band_send_event")
    band_calls.assert_fired("band_send_event")
    call = next(call for call in band_calls if event_marker in str(call.args))
    assert call.tool_call_id
    assert any(
        result.tool_call_id == call.tool_call_id and not result.is_error
        for result in results.named("band_send_event")
    )
    session_ids = {
        getattr(task.metadata, "get", lambda _key: None)("acp_client_session_id")
        for task in tasks
    }
    assert session_ids - {None}
    assert len(session_ids - {None}) == 1


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@flaky_model("OMP may choose a non-file path despite the explicit guarded request")
@pytest.mark.timeout(extra=240)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_permission_resolver_cancels_a_guarded_write(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Path,
) -> None:
    """A resolver cancellation keeps a guarded file unchanged and narrates denial."""
    from band.adapters.omp_acp import OmpACPAdapter  # noqa: PLC0415 -- the ACP extra is optional outside this backend lane

    guarded_file = tmp_path / "guarded.txt"
    original = "do not replace"
    guarded_file.write_text(original)
    observed_calls = []

    async def deny_guarded_write(request: Any) -> str | None:
        arguments = request.tool_call.arguments
        if str(guarded_file) in {str(value) for value in arguments.values()}:
            observed_calls.append(request.tool_call)
            return None
        return select_allow_option_id(request.options)

    identity = await resource_manager.provision_agent("omp-acp-permissions")
    room_id = await resource_manager.provision_room(
        title="e2e-omp-acp-permissions", participants=[identity.id]
    )
    adapter = OmpACPAdapter(
        omp_config(
            baseline_settings,
            tmp_path,
            resolve_permission=deny_guarded_write,
        )
    )

    async with running_agent(identity, adapter, baseline_settings):
        async with reply_capture(room_id) as capture:
            message_id = await user_ops.send_message(
                room_id,
                f"Use your file-writing tool to replace {guarded_file} with 'changed'.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(message_id, identity.id)
            calls = await capture.tool_calls(sender_id=identity.id)
            results = await capture.tool_results(sender_id=identity.id)

    assert observed_calls
    denied_call = observed_calls[0]
    assert guarded_file.read_text() == original
    assert any(call.tool_call_id == denied_call.tool_call_id for call in calls)
    assert any(
        result.tool_call_id == denied_call.tool_call_id and result.is_error
        for result in results
    )
