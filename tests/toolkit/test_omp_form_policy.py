"""Exercise the smoke's narrow policy at OMP's native ACP boundaries."""

from __future__ import annotations

import json

import pytest
from acp.schema import (
    AcceptElicitationResponse,
    DeclineElicitationResponse,
    ElicitationFormSessionMode,
    ElicitationSchema,
    ElicitationStringPropertySchema,
    PermissionOption,
)
from pydantic import JsonValue

from band.adapters.omp_acp import OmpACPAdapter
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.types import ACPToolCall
from band.integrations.omp import (
    OMP_APPROVAL_FORM_TOOL_NAME,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
)
from band.runtime.tools.types import BandTool
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.e2e.baseline.smoke.samples.ompapproval import (
    OMP_TOOL_APPROVAL_HEADER,
    deny_probe_allow_completion,
)

FORM_PROBE_TITLE = "Approve the E2E form probe?"


@pytest.fixture
def adapter() -> OmpACPAdapter:
    return OmpACPAdapter(resolve_permission=deny_probe_allow_completion)


@pytest.fixture
def tools() -> FakeAgentTools:
    return FakeAgentTools()


@pytest.fixture
def emitter(tools: FakeAgentTools) -> RoomTurnEmitter:
    return RoomTurnEmitter(tools, session_id="session", room_id="room")


@pytest.fixture
def form_mode() -> ElicitationFormSessionMode:
    return ElicitationFormSessionMode(
        session_id="session",
        tool_call_id=None,
        requested_schema=ElicitationSchema(
            type="object",
            properties={
                "choice": ElicitationStringPropertySchema(
                    type="string", enum=[OMP_FORM_APPROVE, OMP_FORM_DENY]
                )
            },
            required=["choice"],
        ),
    )


def approval_message(tool: str) -> str:
    return f"{OMP_TOOL_APPROVAL_HEADER}mcp__band_{tool.removeprefix('band_')}\nArguments: {{}}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [BandTool.SEND_MESSAGE, BandTool.NO_REPLY])
async def test_native_probe_denied_then_completion_accepted(
    adapter: OmpACPAdapter,
    emitter: RoomTurnEmitter,
    tools: FakeAgentTools,
    form_mode: ElicitationFormSessionMode,
    tool: BandTool,
) -> None:
    client = adapter._runtime_client_factory()
    handler = adapter._make_elicitation_handler(emitter, "room", "session")
    assert handler is not None
    client.set_elicitation_handler("session", handler)

    denied = await client.create_elicitation(FORM_PROBE_TITLE, form_mode)
    accepted = await client.create_elicitation(approval_message(tool), form_mode)

    assert isinstance(denied, DeclineElicitationResponse)
    assert isinstance(accepted, AcceptElicitationResponse)
    assert accepted.content == {"choice": OMP_FORM_APPROVE}
    calls = events_of_type(tools, "tool_call")
    results = events_of_type(tools, "tool_result")
    assert len(calls) == len(results) == 1
    call = json.loads(calls[0]["content"])
    result = json.loads(results[0]["content"])
    assert call["args"] == {"message": FORM_PROBE_TITLE}
    assert result["tool_call_id"] == call["tool_call_id"]
    assert result["is_error"]
    assert "cancelled" in result["output"].lower()
    assert results[0]["metadata"]["permission_outcome"] == "cancelled"
    assert not tools.turn.complete
    assert tools.chat == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        FORM_PROBE_TITLE,
        "Allow tool: mcp__other_band_send_message",
        "Allow tool: mcp__band_send_event",
        "Allow tool: bash",
        "Allow tool: band_send_message",
        " Allow tool: mcp__band_send_message",
        "Allow tool: mcp__band_send_message suffix",
        "Allow tool: mcp__band_send_message ",
        "Allow tool: mcp__band_send_message\r\nArguments: {}",
        "Description\nAllow tool: mcp__band_send_message",
    ],
)
async def test_native_forms_cannot_broaden_approval(
    adapter: OmpACPAdapter,
    emitter: RoomTurnEmitter,
    form_mode: ElicitationFormSessionMode,
    message: str,
) -> None:
    handler = adapter._make_elicitation_handler(emitter, "room", "session")
    assert handler is not None
    response = await handler(message=message, mode=form_mode)
    assert isinstance(response, DeclineElicitationResponse)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"message": None}, {"message": 42}])
async def test_malformed_synthetic_requests_are_denied(
    arguments: dict[str, JsonValue],
) -> None:
    request = ACPPermissionRequest(
        room_id="room",
        session_id="session",
        tool_call=ACPToolCall(
            tool_call_id="call", name=OMP_APPROVAL_FORM_TOOL_NAME, arguments=arguments
        ),
        options=(),
    )
    assert await deny_probe_allow_completion(request) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("one_shot", [True, False])
async def test_policy_selects_only_the_offered_one_shot_option(one_shot: bool) -> None:
    options = [
        PermissionOption(option_id="persistent", name="Always", kind="allow_always")
    ]
    if one_shot:
        options.append(
            PermissionOption(option_id="fixture-once", name="Once", kind="allow_once")
        )
    request = ACPPermissionRequest(
        room_id="room",
        session_id="session",
        tool_call=ACPToolCall(
            tool_call_id="call",
            name=OMP_APPROVAL_FORM_TOOL_NAME,
            arguments={"message": approval_message(BandTool.SEND_MESSAGE)},
        ),
        options=tuple(options),
    )
    assert await deny_probe_allow_completion(request) == (
        "fixture-once" if one_shot else None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", [BandTool.SEND_MESSAGE, "mcp__band_send_message", "bash"]
)
async def test_direct_native_permissions_remain_cancelled(
    adapter: OmpACPAdapter, emitter: RoomTurnEmitter, name: str
) -> None:
    handler = adapter._make_permission_handler(emitter, "room")
    response = await handler(
        options=[PermissionOption(option_id="once", name="Once", kind="allow_once")],
        session_id="session",
        tool_call={
            "toolCallId": "native",
            "title": name,
            "rawInput": {"message": approval_message(BandTool.SEND_MESSAGE)},
        },
    )
    assert response == {"outcome": {"outcome": "cancelled"}}
