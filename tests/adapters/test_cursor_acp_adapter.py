"""Behavioral tests for the Cursor ACP backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from acp.schema import PermissionOption

from band.adapters.cursor_acp import (
    DEFAULT_CURSOR_ACP_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
    CursorTurn,
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


class _FailingDecisionTools(_DecisionTools):
    def __init__(self, *, fail_after: int = 0) -> None:
        super().__init__()
        self._fail_after = fail_after

    async def send_message(
        self, content: str, mentions: list[str] | None = None
    ) -> None:
        if len(self.messages) >= self._fail_after:
            raise RuntimeError("room delivery failed")
        await super().send_message(content, mentions)


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

    def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(cwd=str(tmp_path)))

        assert adapter._workspace("room-a") == str(tmp_path / "room-a")

    def test_rejects_ambiguous_workspace_config(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="either cwd or workspace_for_room"):
            CursorACPAdapter(
                CursorACPAdapterConfig(
                    cwd=str(tmp_path),
                    workspace_for_room=lambda room_id: str(tmp_path / room_id),
                )
            )

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
        turn = CursorTurn("room-1", tools, "user-1", "session-1")  # type: ignore[arg-type]
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
        assert "agent=Agent" in tools.messages[0]
        assert "plan=Plan" in tools.messages[0]
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
    async def test_manual_multi_question_rejects_an_unauthorized_answer_then_resolves(
        self,
    ) -> None:
        tools = _DecisionTools()
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(decision_authorized_senders=frozenset({"owner"}))
        )
        turn = CursorTurn("room-1", tools, "requester", "session-1")  # type: ignore[arg-type]
        adapter._active_turn = turn
        pending = asyncio.create_task(
            adapter._resolve_question(
                turn,
                {
                    "questions": [
                        {
                            "id": "files",
                            "prompt": "Choose files",
                            "allowMultiple": True,
                            "options": [
                                {"id": "readme", "label": "README"},
                                {"id": "config", "label": "Config"},
                            ],
                        },
                        {
                            "id": "mode",
                            "prompt": "Choose mode",
                            "options": [{"id": "plan", "label": "Plan"}],
                        },
                    ]
                },
            )
        )
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=(f"/cursor answer {token} files=readme,config mode=plan"),
                    sender_id="intruder",
                ),
            ),
            tools,  # type: ignore[arg-type]
            "room-1",
        )

        assert not pending.done()
        assert (
            tools.messages[-1] == "You are not authorized to resolve Cursor decisions."
        )

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=(f"/cursor answer {token} files=readme,config mode=plan"),
                    sender_id="owner",
                ),
            ),
            tools,  # type: ignore[arg-type]
            "room-1",
        )

        assert await pending == {
            "outcome": {
                "outcome": "answered",
                "answers": [
                    {
                        "questionId": "files",
                        "selectedOptionIds": ["readme", "config"],
                    },
                    {"questionId": "mode", "selectedOptionIds": ["plan"]},
                ],
            }
        }

    def test_duplicate_question_answer_is_rejected(self) -> None:
        result = CursorACPAdapter._answer_result(
            ["mode=agent", "mode=plan"],
            {"mode": ("agent", "plan")},
            frozenset(),
        )

        assert not isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_auto_question_uses_the_first_advertised_option(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(question_mode="auto_first"))
        turn = CursorTurn("room-1", _DecisionTools(), "user-1", "session-1")  # type: ignore[arg-type]

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
        adapter._active_turn = CursorTurn("room-1", tools, "user-1", "session-1")  # type: ignore[arg-type]
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

    @pytest.mark.asyncio
    async def test_manual_permission_can_select_an_advertised_option(self) -> None:
        tools = _DecisionTools()
        adapter = CursorACPAdapter()
        adapter._active_turn = CursorTurn("room-1", tools, "user-1", "session-1")  # type: ignore[arg-type]
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
                SimpleNamespace(
                    content=f"/cursor select {token} allow-once", sender_id="user-1"
                ),
            ),
            tools,  # type: ignore[arg-type]
            "room-1",
        )

        assert await pending == "allow-once"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("plan_mode", "outcome"),
        [("auto_accept", "accepted"), ("auto_decline", "rejected")],
    )
    async def test_automatic_plan_policy_returns_its_outcome(
        self, plan_mode: str, outcome: str
    ) -> None:
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(plan_mode=cast(object, plan_mode))  # type: ignore[arg-type]
        )

        result = await adapter._resolve_plan(
            CursorTurn("room-1", _DecisionTools(), "user-1", "session-1"),  # type: ignore[arg-type]
            {"plan": "Plan"},
        )

        assert result == {"outcome": {"outcome": outcome}}

    @pytest.mark.asyncio
    async def test_manual_plan_accepts_a_room_command(self) -> None:
        tools = _DecisionTools()
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._resolve_plan(
                CursorTurn("room-1", tools, "user-1", "session-1"),  # type: ignore[arg-type]
                {"plan": "Plan"},
            )
        )
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor accept {token}", sender_id="user-1"),
            ),
            tools,  # type: ignore[arg-type]
            "room-1",
        )

        assert await pending == {"outcome": {"outcome": "accepted"}}

    @pytest.mark.asyncio
    async def test_automatic_permission_policies_use_offered_options(self) -> None:
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

        accepted = CursorACPAdapter(CursorACPAdapterConfig(approval_mode="auto_accept"))
        declined = CursorACPAdapter(
            CursorACPAdapterConfig(approval_mode="auto_decline")
        )

        assert await accepted._resolve_cursor_permission(request) == "allow-once"
        assert await declined._resolve_cursor_permission(request) is None

    @pytest.mark.asyncio
    async def test_decision_delivery_failure_cleans_up_the_pending_token(self) -> None:
        adapter = CursorACPAdapter()

        result = await adapter._wait_for_decision(
            kind="plan",
            turn=CursorTurn("room-1", _FailingDecisionTools(), "user-1", "session-1"),  # type: ignore[arg-type]
            prompt="Plan {token}",
        )

        assert result is None
        assert adapter._pending_decisions == {}

    @pytest.mark.asyncio
    async def test_timeout_notice_failure_still_cancels_the_decision(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(decision_timeout_s=0.001))

        result = await adapter._wait_for_decision(
            kind="plan",
            turn=CursorTurn(
                "room-1", _FailingDecisionTools(fail_after=1), "user-1", "session-1"
            ),  # type: ignore[arg-type]
            prompt="Plan {token}",
        )

        assert result is None
        assert adapter._pending_decisions == {}

    @pytest.mark.asyncio
    async def test_room_cleanup_cancels_only_its_pending_decision(self) -> None:
        first_tools = _DecisionTools()
        second_tools = _DecisionTools()
        adapter = CursorACPAdapter()
        first = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=CursorTurn("room-1", first_tools, "user-1", "session-1"),  # type: ignore[arg-type]
                prompt="Plan {token}",
            )
        )
        second = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=CursorTurn("room-2", second_tools, "user-2", "session-2"),  # type: ignore[arg-type]
                prompt="Plan {token}",
            )
        )
        await asyncio.gather(
            first_tools.prompt_sent.wait(), second_tools.prompt_sent.wait()
        )
        tokens = {
            decision.room_id: token
            for token, decision in adapter._pending_decisions.items()
        }

        await adapter.on_cleanup("room-1")

        assert await first is None
        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor accept {tokens['room-1']}", sender_id="user-1"
                ),
            ),
            first_tools,  # type: ignore[arg-type]
            "room-1",
        )
        assert first_tools.messages[-1] == (
            f"Cursor decision `{tokens['room-1']}` is not pending."
        )

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor accept {tokens['room-2']}", sender_id="user-2"
                ),
            ),
            second_tools,  # type: ignore[arg-type]
            "room-2",
        )

        assert await second == {"outcome": {"outcome": "accepted"}}
