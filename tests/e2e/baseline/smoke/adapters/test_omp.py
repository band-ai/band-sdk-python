"""Live smoke coverage for OMP native RPC and host tools."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.core.types import MessageType
from band.integrations.omp import (
    finalize_omp_command,
    omp_command_in_workspace,
)
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.e2e.baseline.agents import Adapter, Lane, lane, with_adapters
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.ompapproval import (
    FORM_PROBE_TITLE,
    deny_probe_allow_completion,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.builders import omp_agent_home_dir, omp_env
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
    from band.adapters.omp import OmpAdapter, OmpPermissionRequest

BAND_EVENT_TOOL_NAME = "band_send_event"
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=1)


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_raw_rpc_prompt_completes(
    baseline_settings: BaselineSettings,
) -> None:
    """Distinguish an OMP/provider stall from a Band delivery failure."""
    from band.integrations.omprpc import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
        OmpRpcClient,
    )

    responses: list[str] = []

    async def receive(event: OmpEvent) -> None:
        message = event.message
        if event.type != OmpEventType.MESSAGE_END or message is None:
            return
        if message.role != "assistant":
            return
        if isinstance(message.content, str):
            responses.append(message.content)
            return
        responses.extend(
            block.text
            for block in message.content
            if block.type == "text" and block.text is not None
        )

    async def reject_tool(_call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult(
            content=[OmpContent(type="text", text="No host tools were registered")],
            is_error=True,
        )

    async def cancel_ui(_request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(cancelled=True)

    with tempfile.TemporaryDirectory(prefix="band-e2e-omp-raw-") as sandbox:
        command = finalize_omp_command(
            omp_command(baseline_settings), model=omp_model(baseline_settings)
        )
        runtime = OmpRpcClient(
            command=omp_command_in_workspace(command, sandbox),
            env=omp_env(baseline_settings, omp_agent_home_dir(sandbox)),
        )
        try:
            async with asyncio.timeout(BUDGET.deadline_s):
                await runtime.start()
                await runtime.prompt(
                    "Reply with one short greeting.",
                    on_event=receive,
                    on_tool=reject_tool,
                    on_ui=cancel_ui,
                )
        except TimeoutError:
            pytest.fail("OMP raw RPC prompt did not settle before the turn deadline")
        finally:
            await runtime.close()

    assert any(text.strip() for text in responses), (
        "OMP raw RPC prompt settled without an assistant text response"
    )


@with_adapters(Adapter.OMP, **TOOL_AGENT)
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("ompevent")
    room_id = await resource_manager.provision_room(
        title="e2e-omp-tool-call", participants=[agent.id]
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


def _denying_omp_adapter(
    settings: BaselineSettings,
    sandbox: str,
    decision: str | None,
    requests: list[OmpPermissionRequest],
) -> OmpAdapter:
    from band.adapters.omp import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
        OmpAdapter,
        OmpAdapterConfig,
    )

    async def deny(request: OmpPermissionRequest) -> str | None:
        requests.append(request)
        return await deny_probe_allow_completion(request, probe_decision=decision)

    return OmpAdapter(
        config=OmpAdapterConfig(
            command=(
                *omp_command(settings),
                f"--extension={REPO_ROOT / 'tests/e2e/baseline/fixtures/omp-permission-probe.js'}",
            ),
            model=omp_model(settings),
            custom_section="Keep replies short.",
            cwd=sandbox,
            env={
                **omp_env(settings, omp_agent_home_dir(sandbox)),
                "OMP_PERMISSION_PROBE_FILE": str(Path(sandbox) / "permission-result"),
            },
        ),
        resolve_permission=deny,
    )


@lane(Lane.BACKENDS)
@requires(Dep.OMP)
@pytest.mark.parametrize("decision", ["Deny", None], ids=["deny", "cancel"])
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_native_permission_is_denied(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    decision: str | None,
) -> None:
    """Deny the native probe without denying the turn's required Band completion."""
    requests: list[OmpPermissionRequest] = []
    with tempfile.TemporaryDirectory(prefix="band-e2e-omp-deny-") as sandbox:
        adapter = _denying_omp_adapter(baseline_settings, sandbox, decision, requests)
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
                tool_results = await capture.tool_results(sender_id=agent.id)

            replies.assert_present()
            probe_calls = [
                call
                for call in tool_calls
                if call.args.get("message") == FORM_PROBE_TITLE
            ]
            if decision is None:
                assert len(probe_calls) == 1, "expected one cancelled permission record"
                probe_call = probe_calls[0]
                assert probe_call.tool_call_id
                probe_results = [
                    result
                    for result in tool_results
                    if result.tool_call_id == probe_call.tool_call_id
                ]
                assert len(probe_results) == 1, (
                    "expected the permission record's result"
                )
                result = probe_results[0]
                assert result.is_error and "cancelled" in result.output.lower()
                assert result.raw.metadata is not None
                assert (
                    result.raw.metadata.model_dump()["permission_outcome"]
                    == "cancelled"
                )
            else:
                # An offered UI selection is not an SDK cancellation outcome.
                assert probe_calls == []
            assert sum(request.title == FORM_PROBE_TITLE for request in requests) == 1
            assert all(
                request.room_id == room_id
                and request.session_id
                and request.options == ("Approve", "Deny")
                for request in requests
            )
            assert (Path(sandbox) / "permission-result").read_text() == (
                decision or "cancelled"
            )
