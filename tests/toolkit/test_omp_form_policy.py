"""Preserve the former form policy at the native OMP permission boundary."""

from __future__ import annotations

import json
from functools import partial

import pytest
from pydantic import ValidationError

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
    OmpPermissionRequest,
)
from band.converters.omp import OmpSessionState
from band.integrations.ompwire import OmpUIRequest
from band.runtime.tools.types import BandTool
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.adapters.test_omp_adapter import PendingRuntime
from tests.e2e.baseline.smoke.samples.ompapproval import (
    FORM_PROBE_TITLE,
    deny_probe_allow_completion,
)
from tests.integrations.acp.conftest import make_platform_message


async def _ready(*_args, **_kwargs) -> None:
    return None


class TestOmpFormPolicy:
    @pytest.mark.parametrize("tool", [BandTool.SEND_MESSAGE, BandTool.NO_REPLY])
    async def test_native_probe_denied_then_completion_accepted(
        self, monkeypatch, tmp_path, tool: BandTool
    ) -> None:
        responses = []

        class PermissionRuntime(PendingRuntime):
            async def prompt(self, _message, *, on_event, on_tool, on_ui):
                for identity, title in (
                    ("probe", FORM_PROBE_TITLE),
                    ("completion", f"Allow tool: {tool}\nArguments: {{}}"),
                ):
                    responses.append(
                        await on_ui(
                            OmpUIRequest(
                                id=identity,
                                method="select",
                                title=title,
                                options=["Approve", "Deny"],
                            )
                        )
                    )

        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", PermissionRuntime)
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", _ready)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_permission=partial(
                deny_probe_allow_completion, probe_decision=None
            ),
        )
        tools = FakeAgentTools()
        try:
            await adapter.on_message(
                make_platform_message("Handle the probe"),
                tools,
                OmpSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room",
            )
            assert responses[0].cancelled
            assert responses[1].value == "Approve"
            calls = events_of_type(tools, "tool_call")
            results = events_of_type(tools, "tool_result")
            assert len(calls) == len(results) == 1
            call = json.loads(calls[0]["content"])
            result = json.loads(results[0]["content"])
            assert call["args"] == {"message": FORM_PROBE_TITLE}
            assert call["tool_call_id"]
            assert result["tool_call_id"] == call["tool_call_id"]
            assert result["is_error"]
            assert "cancelled" in result["output"].lower()
            assert results[0]["metadata"]["permission_outcome"] == "cancelled"
            assert not tools.turn.complete
            assert tools.chat == []
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize(
        "title",
        [
            FORM_PROBE_TITLE,
            "Allow tool: mcp__other_band_send_message",
            "Allow tool: band_send_event",
            "Allow tool: bash",
            "Allow tool: mcp__band_send_message",
            " Allow tool: band_send_message",
            "Allow tool: band_send_message suffix",
            "Allow tool: band_send_message ",
            "Allow tool: band_send_message\r\nArguments: {}",
            "Description\nAllow tool: band_send_message",
        ],
    )
    async def test_native_permissions_cannot_broaden_approval(self, title: str) -> None:
        request = OmpPermissionRequest(
            room_id="room",
            session_id="session",
            title=title,
            options=("Approve", "Deny"),
        )
        assert await deny_probe_allow_completion(request, probe_decision=None) is None

    @pytest.mark.parametrize("title", [None, "", "42"])
    async def test_malformed_synthetic_requests_are_denied(
        self, title: str | None
    ) -> None:
        adapter = OmpAdapter(
            resolve_permission=partial(deny_probe_allow_completion, probe_decision=None)
        )
        response = await adapter._permission(
            OmpUIRequest(id="malformed", method="select", title=title, options=[]),
            "room",
            "session",
            FakeAgentTools(),
        )
        assert response.cancelled

    def test_numeric_permission_title_is_rejected_before_policy(self) -> None:
        with pytest.raises(ValidationError):
            OmpUIRequest.model_validate(
                {"id": "malformed", "method": "select", "title": 42, "options": []}
            )

    @pytest.mark.parametrize("one_shot", [True, False])
    async def test_policy_selects_only_the_offered_one_shot_option(
        self, one_shot: bool
    ) -> None:
        request = OmpPermissionRequest(
            room_id="room",
            session_id="session",
            title="Allow tool: band_send_message",
            options=("Approve", "Deny") if one_shot else ("Always", "Deny"),
        )
        assert await deny_probe_allow_completion(request, probe_decision=None) == (
            "Approve" if one_shot else None
        )

    @pytest.mark.parametrize(
        "name", [BandTool.SEND_MESSAGE, "mcp__band_send_message", "bash"]
    )
    async def test_direct_native_permissions_remain_cancelled(self, name: str) -> None:
        adapter = OmpAdapter(
            resolve_permission=partial(deny_probe_allow_completion, probe_decision=None)
        )
        response = await adapter._permission(
            OmpUIRequest(
                id="direct", method="select", title=name, options=["Approve", "Deny"]
            ),
            "room",
            "session",
            FakeAgentTools(),
        )
        assert response.cancelled

    @pytest.mark.parametrize(
        ("method", "choice", "options", "cancelled", "pairs"),
        [
            ("select", "Approve", ["Approve", "Deny"], False, 0),
            ("select", "Deny", ["Approve", "Deny"], False, 0),
            ("select", "Reject", ["Accept", "Reject"], False, 0),
            ("select", None, ["Accept", "Reject"], True, 1),
            ("select", "Deny", ["Accept", "Reject"], True, 1),
            ("select", "Always", ["Approve", "Deny"], True, 1),
            ("notify", "Approve", ["Approve", "Deny"], True, 0),
        ],
    )
    async def test_only_denied_selects_are_narrated_without_turn_effects(
        self,
        method: str,
        choice: str | None,
        options: list[str],
        cancelled: bool,
        pairs: int,
    ) -> None:
        async def resolve(_request: OmpPermissionRequest) -> str | None:
            return choice

        adapter = OmpAdapter(resolve_permission=resolve)
        tools = FakeAgentTools()
        response = await adapter._permission(
            OmpUIRequest(
                id="decision",
                method=method,
                title="Allow tool: write",
                options=options,
            ),
            "room",
            "session",
            tools,
        )
        assert response.cancelled is cancelled
        assert len(events_of_type(tools, "tool_call")) == pairs
        results = events_of_type(tools, "tool_result")
        assert len(results) == pairs
        if pairs:
            assert results[0]["metadata"]["permission_outcome"] == "cancelled"
        assert not tools.turn.complete
        assert tools.chat == []

    async def test_permission_narration_respects_disabled_tool_events(self) -> None:
        adapter = OmpAdapter(
            emit=(),
            resolve_permission=partial(
                deny_probe_allow_completion, probe_decision=None
            ),
        )
        tools = FakeAgentTools()
        denied = await adapter._permission(
            OmpUIRequest(id="probe", method="select", title=FORM_PROBE_TITLE),
            "room",
            "session",
            tools,
        )
        assert denied.cancelled
        assert events_of_type(tools, "tool_call") == []
        assert events_of_type(tools, "tool_result") == []
        assert not tools.turn.complete
        assert tools.chat == []
