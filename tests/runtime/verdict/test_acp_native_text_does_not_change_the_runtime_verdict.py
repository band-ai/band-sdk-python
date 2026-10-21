from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.core.types import (
    Emit,
)
from band.runtime.tools import BandTool
from tests.runtime.helpers import (
    deliver,
    run_through,
)
from tests.runtime.verdict.kit import (
    NATIVE_OUTCOMES,
    assert_runtime_outcome,
)


class TestAcpNativeTextDoesNotChangeTheRuntimeVerdict:
    @pytest.mark.parametrize("path", ["live", "backlog"])
    @pytest.mark.parametrize("thoughts", [True, False])
    @pytest.mark.parametrize(
        "row",
        [
            "native-only",
            "invalid-decline",
            "decline",
            "reply",
            "act",
            "failed-then-decline",
        ],
    )
    async def test_acp_native_text_does_not_change_the_runtime_verdict(
        self,
        link: MagicMock,
        path: str,
        thoughts: bool,
        row: str,
    ) -> None:
        pytest.importorskip(
            "acp", reason="ACP extra is tested in its supported dependency lane"
        )
        from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- optional ACP extra
            ACPClientAdapter,
        )
        from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- optional ACP extra
            FakeACPAgent,
            fake_agent_config,
            started_acp_adapter,
        )

        calls, completes = NATIVE_OUTCOMES[row]
        agent = FakeACPAgent()
        for index, (name, arguments) in enumerate(calls):
            if name == BandTool.NO_REPLY and arguments.get("reason") == 123:
                agent.will_call_invalid_mcp_tool(str(index), name, arguments=arguments)
            else:
                agent.will_call_mcp_tool(str(index), name, arguments=arguments)
        agent.will_say("Closing narration")
        adapter = ACPClientAdapter(
            fake_agent_config(inject_band_tools=True),
            emit={Emit.THOUGHTS} if thoughts else set(),
        )
        async with started_acp_adapter(adapter, agent):
            await deliver(run_through(adapter, link), path)
        assert_runtime_outcome(link, completes)
