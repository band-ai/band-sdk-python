"""Behavioral tests for the Cursor ACP backend."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from acp.schema import PermissionOption

from band.adapters.cursor_acp import (
    DEFAULT_CURSOR_ACP_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
    _CursorTurn,
)
from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.session_config import ACPConfigRequest
from band.integrations.acp.types import ACPToolCall


class _DecisionTools:
    """Records room prompts and signals once a pending decision is visible."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.prompt_sent = asyncio.Event()

    async def send_message(
        self, content: str, mentions: list[str] | None = None
    ) -> None:
        del mentions
        self.messages.append(content)
        self.prompt_sent.set()


class TestCursorACPAdapterConstruction:
    def test_uses_cursor_acp_and_the_current_extension_profile(self) -> None:
        adapter = CursorACPAdapter()

        assert adapter._command == list(DEFAULT_CURSOR_ACP_COMMAND)
        assert adapter._auth_method == "cursor_login"
        assert adapter._profile is adapter._cursor_profile

    def test_auth_convenience_values_do_not_override_explicit_env(self) -> None:
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(
                api_key="shortcut",
                env={"CURSOR_API_KEY": "environment"},
            )
        )

        assert adapter._env == {"CURSOR_API_KEY": "environment"}

    def test_rejects_ambiguous_auth(self) -> None:
        with pytest.raises(ValueError, match="either api_key or auth_token"):
            CursorACPAdapter(CursorACPAdapterConfig(api_key="a", auth_token="b"))

    def test_forwards_the_live_session_catalog_resolver(self) -> None:
        async def resolve(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {"model": "advertised-model"}

        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(resolve_session_config=resolve)
        )

        assert adapter._resolve_session_config is resolve


class TestCursorACPAdapterDecisions:
    @pytest.mark.asyncio
    async def test_manual_question_requires_a_valid_room_answer(self) -> None:
        tools = _DecisionTools()
        adapter = CursorACPAdapter()
        turn = _CursorTurn("room-1", tools, "user-1", "session-1")  # type: ignore[arg-type]
        adapter._active_turn = turn

        pending = asyncio.create_task(
            adapter._resolve_question(
                turn,
                {
                    "questions": [
                        {
                            "id": "mode",
                            "prompt": "Choose mode",
                            "options": [
                                {"id": "agent", "label": "Agent"},
                                {"id": "plan", "label": "Plan"},
                            ],
                        }
                    ]
                },
            )
        )
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor answer {token} mode=plan", sender_id="user-1"
                ),
            ),
            tools,  # type: ignore[arg-type]
            "room-1",
        )

        assert handled is True
        assert await pending == {
            "outcome": {
                "outcome": "answered",
                "answers": [{"questionId": "mode", "selectedOptionIds": ["plan"]}],
            }
        }

    @pytest.mark.asyncio
    async def test_auto_question_uses_the_first_advertised_option(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(question_mode="auto_first"))
        turn = _CursorTurn("room-1", _DecisionTools(), "user-1", "session-1")  # type: ignore[arg-type]

        result = await adapter._resolve_question(
            turn,
            {
                "questions": [
                    {
                        "id": "mode",
                        "prompt": "Choose mode",
                        "options": [
                            {"id": "agent", "label": "Agent"},
                            {"id": "plan", "label": "Plan"},
                        ],
                    }
                ]
            },
        )

        assert cast(dict[str, object], cast(dict[str, object], result)["outcome"])[
            "answers"
        ] == [{"questionId": "mode", "selectedOptionIds": ["agent"]}]

    @pytest.mark.asyncio
    async def test_manual_permission_can_be_denied_from_the_room(self) -> None:
        tools = _DecisionTools()
        adapter = CursorACPAdapter()
        adapter._active_turn = _CursorTurn("room-1", tools, "user-1", "session-1")  # type: ignore[arg-type]
        request = ACPPermissionRequest(
            room_id="room-1",
            session_id="session-1",
            tool_call=ACPToolCall("call-1", "shell", {}),
            options=(
                PermissionOption(
                    optionId="allow-once", name="Allow", kind="allow_once"
                ),
            ),
        )
        pending = asyncio.create_task(adapter._resolve_cursor_permission(request))
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor deny {token}", sender_id="user-1"),
            ),
            tools,  # type: ignore[arg-type]
            "room-1",
        )

        assert await pending is None
