"""Successful host declines stop native continuation and retain room usability."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.integrations.omprpc.errors import OmpPromptError
from band.integrations.ompwire import OmpPromptStatus
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.omp.declinekit import (
    ConcurrentDeclineTools,
    CustomDeclineInput,
    DeclineTools,
    custom_decline,
    declining_adapter,
    stop_commands,
    tool_results,
)
from tests.adapters.omp.declinepeer import DECLINE_CALL, REPLY_TEXT, DeclineScenario
from tests.adapters.omp.kit import run_turn


class TestTerminalDecline:
    async def test_concurrent_declines_share_one_owned_stop(
        self, tmp_path: Path
    ) -> None:
        tools = ConcurrentDeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.CONCURRENT) as adapter:
            await run_turn(adapter, tools)

            assert tools.started == 2
            assert len(stop_commands(tmp_path)) == 1
            assert len(tool_results(tmp_path)) == 1
            assert failure_reports(tools) == []

    async def test_successful_decline_stops_later_calls_and_allows_next_turn(
        self, tmp_path: Path
    ) -> None:
        declined = DeclineTools()
        follow_up = DeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.SUCCESS) as adapter:
            await run_turn(adapter, declined)

            assert declined.chat == []
            assert failure_reports(declined) == []
            assert [call["tool_name"] for call in declined.tool_calls] == [
                BandTool.NO_REPLY
            ]
            results = tool_results(tmp_path)
            assert len(results) == 1
            assert results[0]["id"] == DECLINE_CALL
            assert not results[0]["isError"]

            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [REPLY_TEXT]
            assert failure_reports(follow_up) == []

    async def test_failed_decline_can_recover_with_a_reply(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools(fail_decline=True)
        async with declining_adapter(tmp_path, DeclineScenario.FAILED) as adapter:
            await run_turn(adapter, tools)

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []
        assert tool_results(tmp_path)[0]["isError"]

    async def test_ordinary_tools_continue_to_a_reply(self, tmp_path: Path) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.ORDINARY) as adapter:
            await run_turn(adapter, tools)

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_custom_tool_named_no_reply_can_continue(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(
            tmp_path,
            DeclineScenario.SUCCESS,
            additional_tools=[(CustomDeclineInput, custom_decline)],
            exclude_tools=(BandTool.NO_REPLY,),
        ) as adapter:
            await run_turn(adapter, tools)

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_unrequested_abort_still_fails(self, tmp_path: Path) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.ABORTED) as adapter:
            with pytest.raises(OmpPromptError):
                await run_turn(adapter, tools)

        assert tools.chat == []
        assert failure_reports(tools)

    async def test_provider_error_is_not_hidden_by_intentional_stop(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(
            tmp_path, DeclineScenario.PROVIDER_ERROR
        ) as adapter:
            with pytest.raises(OmpPromptError) as failure:
                await run_turn(adapter, tools)

        assert failure.value.status == OmpPromptStatus.ERROR
        assert tools.chat == []
