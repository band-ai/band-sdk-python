"""WireBudget behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)
from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing import (
    failure_reports,
)
from tests.adapters.genaikit import (
    text_reply,
    tool_call,
)
from tests.adapters.googleadk.bridge import (
    GoogleADKAdapter,
    GoogleADKAdapterConfig,
    _google_adk_mod,
)
from tests.adapters.googleadk.httppeer import (
    EFFECT_ARGS,
    GoogleHTTPPeer,
    HTTPMode,
    RecoveryTools,
)
from tests.adapters.usage_events import recorded_usage_payloads
from tests.framework_conformance.turnprobes import (
    turn_input,
)


class TestWireBudget:
    @pytest.mark.parametrize("google_http", [HTTPMode.PROXY], indirect=True)
    @pytest.mark.asyncio
    async def test_generation_preserves_environment_proxy_routing(
        self, google_http: GoogleHTTPPeer
    ) -> None:
        google_http.responses = [
            tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS),
            text_reply("Done"),
        ]
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        await adapter.on_event(turn_input(tools))
        assert tools.chat == ["Completed effect"]
        assert len(google_http.requests) == 2
        assert google_http.clients_closed

    @pytest.mark.parametrize("google_http", [HTTPMode.TCP], indirect=True)
    @pytest.mark.asyncio
    async def test_provider_clients_close_when_runner_cleanup_fails(
        self, google_http: GoogleHTTPPeer
    ) -> None:
        google_http.responses = [
            tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS),
            text_reply("Done"),
        ]
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")
        _, runner_class, _, _, _ = _google_adk_mod._require_adk()
        with (
            patch.object(
                runner_class,
                "close",
                new=AsyncMock(side_effect=RuntimeError("Tool cleanup failed")),
            ),
            pytest.raises(RuntimeError, match="Tool cleanup failed"),
        ):
            await adapter.on_event(turn_input(RecoveryTools()))
        assert google_http.clients_closed

    @pytest.mark.parametrize("google_http", [HTTPMode.TCP], indirect=True)
    @pytest.mark.parametrize("post_tool", [False, True], ids=["initial", "post-tool"])
    @pytest.mark.asyncio
    async def test_zero_budget_never_resends_a_disconnected_request(
        self, google_http: GoogleHTTPPeer, post_tool: bool
    ) -> None:
        google_http.responses = [
            *([tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS)] if post_tool else []),
            None,
            *([] if post_tool else [tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS)]),
            text_reply("Done"),
        ]
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_retries=0), emit={Emit.USAGE}
        )
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        with pytest.raises(httpx.RemoteProtocolError):
            await adapter.on_event(turn_input(tools))
        assert len(google_http.requests) == (2 if post_tool else 1)
        assert tools.chat == (["Completed effect"] if post_tool else [])
        assert failure_reports(tools) == [
            ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
        ]
        assert len(recorded_usage_payloads(tools)) == (1 if post_tool else 0)
        assert google_http.clients_closed

    def test_negative_retry_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            GoogleADKAdapterConfig(max_retries=-1)
