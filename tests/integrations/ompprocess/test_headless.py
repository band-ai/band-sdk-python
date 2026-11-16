"""Band launches native OMP with headless extensions and no interactive ask tool.

These tests start the installed ``omp`` executable with the SDK-owned plugin
and launch arguments. They never run inference: startup and the probe are
slash commands, and the isolated home holds no real provider credentials.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel, Field

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")

from band.integrations.omp import finalize_omp_command
from band.integrations.ompprocess import (
    DEFAULT_OMP_COMMAND,
    omp_mcp_arguments,
    omp_mcp_plugin,
    omp_mcp_ready,
    omp_rpc_command_in_workspace,
    omp_runtime_env,
)
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpCommandName,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)

pytestmark = [
    pytest.mark.skipif(
        shutil.which(DEFAULT_OMP_COMMAND[0]) is None, reason="omp is not installed"
    ),
    pytest.mark.timeout(120),
]

# OMP refuses to start without a selectable model; the key is never sent
# because no turn reaches inference.
MODEL = "anthropic/claude-sonnet-4-5"
UNUSED_API_KEY = "band-sdk-headless-test-unused"
BAND_RULES = "Reply through Band."
ASK_TOOL = "ask"
READ_TOOL = "read"
PROBE_COMMAND = "band-sdk-headless-probe"
STARTUP_BUDGET_S = 90


class NativeTool(BaseModel):
    name: str


class NativeToolState(BaseModel):
    dumpTools: list[NativeTool]


class HeadlessReceipt(BaseModel):
    hasUI: bool = Field(strict=True)


@dataclass(frozen=True)
class NativeSession:
    runtime: OmpRpcClient
    receipt: Path


def probe_extension(receipt: Path) -> str:
    """An extension command that records what extensions see as their UI."""
    return f"""import {{ writeFileSync }} from "node:fs";

export default function (pi) {{
  pi.registerCommand({json.dumps(PROBE_COMMAND)}, {{
    description: "Record whether extensions can prompt a user",
    handler: async (_args, ctx) => {{
      writeFileSync({json.dumps(str(receipt))}, JSON.stringify({{ hasUI: ctx.hasUI }}));
    }},
  }});
}}
"""


@pytest.fixture(scope="module")
def omp_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One isolated home per module, so OMP unpacks its natives only once."""
    return tmp_path_factory.mktemp("omp-home")


@pytest_asyncio.fixture(loop_scope="function")
async def band_session(tmp_path: Path, omp_home: Path) -> AsyncIterator[NativeSession]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # An operator overlay that re-enables ask must not outrank the SDK overlay.
    operator_config = tmp_path / "operator.json"
    operator_config.write_text(json.dumps({"ask": {"enabled": True}}), "utf-8")
    probe = tmp_path / "probe.js"
    receipt = tmp_path / "probe-receipt.json"
    probe.write_text(probe_extension(receipt), "utf-8")
    operator_command = (
        *DEFAULT_OMP_COMMAND,
        "--config",
        str(operator_config),
        "--extension",
        str(probe),
    )
    isolation = {
        "HOME": str(omp_home),
        "USERPROFILE": str(omp_home),
        "PI_CODING_AGENT_DIR": str(tmp_path / "agent"),
    }

    with omp_mcp_plugin(
        {}, workspace=str(workspace), system_prompt=BAND_RULES
    ) as plugin:
        assert plugin is not None
        command = omp_rpc_command_in_workspace(
            finalize_omp_command(operator_command, model=MODEL), str(workspace)
        )
        command.extend(omp_mcp_arguments(plugin))
        runtime = OmpRpcClient(
            command,
            env=omp_runtime_env(env=isolation, model=MODEL, api_key=UNUSED_API_KEY),
        )
        try:
            async with asyncio.timeout(STARTUP_BUDGET_S):
                await runtime.start()
                await omp_mcp_ready(runtime, plugin, {})
            yield NativeSession(runtime, receipt)
        finally:
            await runtime.close()


class TestHeadlessNativeOmp:
    async def test_band_sessions_offer_no_interactive_ask_tool(
        self, band_session: NativeSession
    ) -> None:
        async with asyncio.timeout(STARTUP_BUDGET_S):
            state = NativeToolState.model_validate(
                await band_session.runtime.request(OmpCommandName.GET_STATE)
            )

        tools = {tool.name for tool in state.dumpTools}
        assert ASK_TOOL not in tools
        assert READ_TOOL in tools

    async def test_extensions_run_without_an_interactive_ui(
        self, band_session: NativeSession
    ) -> None:
        events: list[str] = []

        async def on_event(event: OmpEvent) -> None:
            events.append(event.type)

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            raise AssertionError(f"probe invoked host tool {call.toolName}")

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            raise AssertionError(f"probe requested UI {request.method}")

        async with asyncio.timeout(STARTUP_BUDGET_S):
            await band_session.runtime.prompt(
                f"/{PROBE_COMMAND}", on_event=on_event, on_tool=on_tool, on_ui=on_ui
            )

        assert OmpEventType.AGENT_START not in events
        receipt = HeadlessReceipt.model_validate_json(
            band_session.receipt.read_text(encoding="utf-8")
        )
        assert receipt.hasUI is False
