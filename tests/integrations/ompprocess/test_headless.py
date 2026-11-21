"""Check native OMP approval routing and ask tool exposure with no live model."""

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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")

from band.adapters.omp import OMP_TOOL_APPROVAL_HEADER, OmpAdapter, OmpAdapterConfig
from band.adapters.omp.config import OmpPermissionRequest
from band.integrations.omp import (
    DEFAULT_OMP_MODEL,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
    finalize_omp_command,
)
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
from band.integrations.uvicorn_server import ManagedUvicornServer
from band.runtime.tools.types import BandTool
from band.testing import FakeAgentTools
from tests.adapters.omp.turns import run_turn
from tests.lifecycle import running

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


class UIReceipt(BaseModel):
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
async def band_session(
    tmp_path: Path, omp_home: Path, request: pytest.FixtureRequest
) -> AsyncIterator[NativeSession]:
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
        *getattr(request, "param", ()),
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


class ReplyProvider:
    def __init__(self, arguments: dict[str, object]) -> None:
        self.arguments = arguments

    async def respond(self, request: Request) -> Response:
        body = await request.json()
        if any(item.get("type") == "function_call_output" for item in body["input"]):
            item = {
                "type": "message",
                "id": "msg-1",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Done.", "annotations": []}
                ],
            }
        else:
            item = {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "call-1",
                "name": BandTool.SEND_MESSAGE,
                "arguments": json.dumps(self.arguments),
            }
        response = {"id": "response-" + item["id"], "status": "completed"}
        events = [
            {"type": "response.created", "response": response},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {
                "type": "response.completed",
                "response": {
                    **response,
                    "output": [item],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            },
        ]
        return Response(
            "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                for event in events
            ),
            media_type="text/event-stream",
        )


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

    @pytest.mark.parametrize(
        ("band_session", "has_ui"),
        [
            pytest.param((), True, id="default"),
            pytest.param(("--no-ui",), False, id="explicit-no-ui"),
        ],
        indirect=["band_session"],
    )
    async def test_extensions_respect_operator_ui_setting(
        self, band_session: NativeSession, has_ui: bool
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
        receipt = UIReceipt.model_validate_json(
            band_session.receipt.read_text(encoding="utf-8")
        )
        assert receipt.hasUI is has_ui

    async def test_band_reply_reaches_native_approval_and_dispatch(
        self, tmp_path: Path, omp_home: Path
    ) -> None:
        arguments: dict[str, object] = {
            "content": "Scripted Band reply",
            "mentions": ["user"],
        }
        server = ManagedUvicornServer(
            Starlette(
                routes=[
                    Route(
                        "/v1/responses",
                        ReplyProvider(arguments).respond,
                        methods=["POST"],
                    )
                ]
            ),
            host="127.0.0.1",
            port=0,
        )
        agent_home = tmp_path / "agent"
        agent_home.mkdir()
        permissions: list[OmpPermissionRequest] = []

        async def approve(request: OmpPermissionRequest) -> str:
            permissions.append(request)
            return OMP_FORM_APPROVE

        async with running(server):
            (agent_home / "models.yml").write_text(
                f"providers:\n  openai:\n    baseUrl: http://127.0.0.1:{server.bound_port}/v1\n",
                encoding="utf-8",
            )
            adapter = OmpAdapter(
                OmpAdapterConfig(
                    cwd=str(tmp_path),
                    model=DEFAULT_OMP_MODEL,
                    api_key=UNUSED_API_KEY,
                    env={
                        "HOME": str(omp_home),
                        "USERPROFILE": str(omp_home),
                        "PI_CODING_AGENT_DIR": str(agent_home),
                    },
                ),
                resolve_permission=approve,
            )
            room_id = "permission-probe"
            tools = FakeAgentTools()
            try:
                async with asyncio.timeout(STARTUP_BUDGET_S):
                    await run_turn(adapter, tools, room_id=room_id)
            finally:
                await adapter.on_cleanup(room_id)

        assert [
            (request.room_id, request.title.splitlines()[0], request.options)
            for request in permissions
        ] == [
            (
                room_id,
                f"{OMP_TOOL_APPROVAL_HEADER}{BandTool.SEND_MESSAGE}",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
            )
        ]
        assert tools.tool_calls == [
            {"tool_name": BandTool.SEND_MESSAGE, "arguments": arguments}
        ]
        assert tools.turn.replied
