"""Slack ingress preserves active-turn controls and ownership."""

from __future__ import annotations

from typing import Any

import pytest

from band.core.exceptions import InputNotAccepted
from band.integrations.codex.types import CodexSessionState
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
    parked_on_approval,
)
from tests.adapters.codexturns import final_text, turn_completed
from tests.integrations.slack.test_wrapping import _make_adapter, _mention_event


class TestSlackControls:
    @pytest.mark.parametrize("entry", ["band", "slack", "recovery"])
    async def test_wrapped_controls_release_original_turn_without_accepting_new_work(
        self, entry: str, codex_room: Any
    ) -> None:
        room = await codex_room(
            *parked_on_approval(final_text("Tests pass."), turn_completed())
        )
        wrapper, _, _, _ = _make_adapter(inner=room.adapter)
        await room.send("run tests")
        original_tools = room.deliveries[0]

        async def deliver(content: str) -> bool | None:
            msg = make_platform_message(content=content)
            tools = ToolSchemaFakeTools()
            match entry:
                case "band":
                    await wrapper.on_message(
                        msg,
                        tools,
                        CodexSessionState(),
                        None,
                        None,
                        is_session_bootstrap=False,
                        room_id=msg.room_id,
                    )
                case "slack":
                    await wrapper._invoke_brain_for_slack_event(
                        wrapper.config.apps[0], _mention_event(text=content)["event"]
                    )
                case "recovery":
                    return await wrapper.try_handle_control(msg, tools)
            return None

        if entry == "recovery":
            assert await deliver("another ordinary request") is False
        else:
            with pytest.raises(InputNotAccepted):
                await deliver("another ordinary request")
        assert room.client.responses == []

        handled = await deliver("/approve req-10")
        if entry == "recovery":
            assert handled is True
        await room.settled()
        assert room.client.responses == [(10, {"decision": "accept"})]
        assert events_of_type(original_tools, "thought")[-1]["content"] == "Tests pass."
