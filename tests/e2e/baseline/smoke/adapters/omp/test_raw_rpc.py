"""Distinguish an OMP/provider stall from a Band delivery failure."""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from band.integrations.omp import finalize_omp_command
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContent,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.omp.kit import BUDGET
from tests.e2e.baseline.toolkit.builders import omp_acp_env, omp_agent_home_dir
from tests.e2e.baseline.toolkit.omp_credentials import native_omp_command, omp_model


class TestOmpRawRpc:
    @lane(Lane.BACKENDS)
    @requires(Dep.OMP_NATIVE)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_omp_raw_rpc_prompt_completes(
        self,
        baseline_settings: BaselineSettings,
    ) -> None:
        from band.integrations.ompprocess import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
            omp_rpc_command_in_workspace,
        )
        from band.integrations.omprpc import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
            OmpRpcClient,
        )

        responses: list[str] = []

        async def receive(event: OmpEvent) -> None:
            message = event.message
            if event.type != OmpEventType.MESSAGE_END or message is None:
                return
            if message.role != OMP_ASSISTANT_ROLE:
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
                native_omp_command(baseline_settings),
                model=omp_model(baseline_settings),
            )
            runtime = OmpRpcClient(
                command=omp_rpc_command_in_workspace(command, sandbox),
                env=omp_acp_env(baseline_settings, omp_agent_home_dir(sandbox)),
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
                pytest.fail(
                    "OMP raw RPC prompt did not settle before the turn deadline"
                )
            finally:
                await runtime.close()

        assert any(text.strip() for text in responses), (
            "OMP raw RPC prompt settled without an assistant text response"
        )
