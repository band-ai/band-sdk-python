"""Drive permission and native record ordering through real RPC."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
)
from band.converters.omp import OmpSessionState
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEventType,
)
from band.testing import FakeAgentTools
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omppermissionpeer import (
    DENIED_PATH,
    PERMISSION_AFTER_NATIVE,
    PermissionOrder,
)
from tests.paths import REPO_ROOT


async def decline_permission(_request) -> None:
    return None


async def owned_rpc_turn(
    monkeypatch,
    tmp_path: Path,
    title: str,
    order: PermissionOrder,
    *,
    native_name: str = "read",
    native_arguments=None,
    native_error: bool = True,
    inject_band_tools: bool = True,
    tools: FakeAgentTools | None = None,
    judged: bool = False,
):
    observed = []

    class OrderingClient(OmpRpcClient):
        async def prompt(self, message, *, on_event, on_tool, on_ui):
            async def event(frame):
                if frame.type in {OmpEventType.TOOL_START, OmpEventType.TOOL_END}:
                    observed.append(frame.type)
                await on_event(frame)
                if (
                    frame.type == OmpEventType.TOOL_START
                    and order == PermissionOrder.NATIVE_FIRST
                ):
                    await self.request(PERMISSION_AFTER_NATIVE)

            async def ui(request):
                observed.append(request.method)
                return await on_ui(request)

            await super().prompt(message, on_event=event, on_tool=on_tool, on_ui=ui)

    def client_factory(command, env=None, **kwargs):
        client = OrderingClient(
            command,
            **kwargs,
            env={
                **(env or {}),
                "PYTHONPATH": str(REPO_ROOT),
                "OMP_TEST_PERMISSION_TITLE": title,
                "OMP_TEST_NATIVE_NAME": native_name,
                "OMP_TEST_NATIVE_ARGUMENTS": json.dumps(
                    {"path": DENIED_PATH}
                    if native_arguments is None
                    else native_arguments
                ),
                "OMP_TEST_NATIVE_ERROR": "1" if native_error else "0",
            },
        )
        return client

    monkeypatch.setattr("band.adapters.omp.lifecycle.OmpRpcClient", client_factory)
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            inject_band_tools=inject_band_tools,
            command=(
                sys.executable,
                "-u",
                "-m",
                "tests.integrations.omppermissionpeer",
                order,
            ),
        ),
        resolve_permission=decline_permission,
    )
    tools = tools if tools is not None else FakeAgentTools()
    turn_input = {
        "msg": make_platform_message("Handle the permission request"),
        "tools": tools,
        "history": OmpSessionState(),
        "participants_msg": None,
        "contacts_msg": None,
        "is_session_bootstrap": True,
        "room_id": "room",
    }
    try:
        if judged:
            await adapter.run_judged_turn(**turn_input)
        else:
            await adapter.on_message(**turn_input)
    finally:
        await adapter.cleanup_all()
    return tools, observed
