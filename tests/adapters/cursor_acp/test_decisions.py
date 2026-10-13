"""Room-driven permission, question and plan decisions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast

import pytest
from acp.schema import PermissionOption

from band.adapters.cursor_acp import (
    DECISION_UNAUTHORIZED_MESSAGE,
    CursorACPAdapter,
    CursorACPAdapterConfig,
)
from band.client.streaming import ControlMode
from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.cursor import PLAN_REQUESTED_TEMPLATE
from band.integrations.acp.permissions import ALLOW_ONCE_KIND
from band.integrations.acp.types import ACPToolCall
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME
from tests.adapters.cursor_acp.kit import (
    CursorRoom,
    DecisionTools,
    FailingDecisionTools,
    activate,
    cursor_turn,
    pending_tokens,
    permission_requests,
    resident,
    room_message,
    said,
)
from tests.integrations.acp.acp_toolkit.agent import (
    FakeACPAgent,
    allow_or_reject,
)
from tests.integrations.acp.acp_toolkit.harness import (
    decision_token,
    pending_decisions,
)


class TestCursorACPAdapterDecisions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "inactive",
        ["absent", "other-room", "other-session", "interrupted", "cleaned-up"],
    )
    @pytest.mark.parametrize("approval_mode", ["manual", "auto_accept", "auto_decline"])
    async def test_band_permission_requires_a_matching_live_turn(
        self, inactive: str, approval_mode: str
    ) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter(CursorACPAdapterConfig(approval_mode=approval_mode))
        if inactive != "absent":
            activate(
                adapter,
                cursor_turn(
                    adapter,
                    "other-room" if inactive == "other-room" else "room-1",
                    tools,
                    "user-1",
                    "other-session" if inactive == "other-session" else "session-1",
                ),
            )
        if inactive == "interrupted":
            await adapter.on_interrupt("room-1", ControlMode.STOP)
        if inactive == "cleaned-up":
            await adapter.on_cleanup("room-1")
        request = ACPPermissionRequest(
            room_id="room-1",
            session_id="session-1",
            tool_call=ACPToolCall(
                "reply",
                adapter._canonical_tool_name(
                    "band-band_send_message: band_send_message"
                ),
                {},
                mcp_server=BAND_MCP_SERVER_NAME,
            ),
            options=(
                PermissionOption(
                    optionId="once", name="Allow once", kind=ALLOW_ONCE_KIND
                ),
            ),
        )

        assert await adapter._resolve_cursor_permission(request) is None
        assert tools.messages == []

    @pytest.mark.asyncio
    async def test_manual_question_requires_a_valid_room_answer(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        turn = cursor_turn(adapter, "room-1", tools, "user-1", "session-1")
        activate(adapter, turn)

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
        token = pending_tokens(adapter)[0]

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor answer {token} mode=plan", sender_id="user-1"
                ),
            ),
            tools,
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
        tools = DecisionTools()
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(decision_authorized_senders=frozenset({"owner"}))
        )
        turn = cursor_turn(adapter, "room-1", tools, "requester", "session-1")
        activate(adapter, turn)
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
        token = pending_tokens(adapter)[0]

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=(f"/cursor answer {token} files=readme,config mode=plan"),
                    sender_id="intruder",
                ),
            ),
            tools,
            "room-1",
        )

        assert not pending.done()
        assert tools.messages[-1] == DECISION_UNAUTHORIZED_MESSAGE

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=(f"/cursor answer {token} files=readme,config mode=plan"),
                    sender_id="owner",
                ),
            ),
            tools,
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

    @pytest.mark.asyncio
    async def test_a_prompt_less_question_is_visible_and_answerable(self) -> None:
        """Regression: a question with no string ``prompt`` was silently
        absent from the summary shown to the user while still being
        required by _answer_result, making a complete answer impossible to
        construct. It must fall back to a displayable label (its id)."""
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        turn = cursor_turn(adapter, "room-1", tools, "user-1", "session-1")
        activate(adapter, turn)

        pending = asyncio.create_task(
            adapter._resolve_question(
                turn,
                {
                    "questions": [
                        {
                            "id": "confirm",
                            "options": [
                                {"id": "yes", "label": "Yes"},
                                {"id": "no", "label": "No"},
                            ],
                        }
                    ]
                },
            )
        )
        await tools.prompt_sent.wait()
        assert "confirm" in tools.messages[0]
        token = pending_tokens(adapter)[0]

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor answer {token} confirm=yes", sender_id="user-1"
                ),
            ),
            tools,
            "room-1",
        )

        assert await pending == {
            "outcome": {
                "outcome": "answered",
                "answers": [{"questionId": "confirm", "selectedOptionIds": ["yes"]}],
            }
        }

    @pytest.mark.asyncio
    async def test_a_question_with_no_answerable_options_cancels_the_whole_exchange(
        self,
    ) -> None:
        """Regression: a question with an empty/unparseable options list used
        to silently vanish from the projected choices, so the exchange could
        be marked 'answered' without Cursor's required question ever being
        answered. It must cancel outright instead."""
        adapter = CursorACPAdapter()
        turn = cursor_turn(adapter, "room-1", DecisionTools(), "user-1", "session-1")

        result = await adapter._resolve_question(
            turn,
            {
                "questions": [
                    {
                        "id": "q1",
                        "prompt": "Pick one",
                        "options": [{"id": "a", "label": "A"}],
                    },
                    {"id": "q2", "prompt": "Malformed", "options": []},
                ]
            },
        )

        assert result == {"outcome": {"outcome": "cancelled"}}

    @pytest.mark.asyncio
    async def test_a_duplicate_question_id_keeps_the_first_occurrence(self) -> None:
        """Regression: two questions sharing an id used to silently collapse
        to whichever was processed last, discarding the earlier question's
        options with no signal."""
        adapter = CursorACPAdapter(CursorACPAdapterConfig(question_mode="auto_first"))
        turn = cursor_turn(adapter, "room-1", DecisionTools(), "user-1", "session-1")

        result = await adapter._resolve_question(
            turn,
            {
                "questions": [
                    {
                        "id": "q1",
                        "prompt": "First q1",
                        "options": [{"id": "a", "label": "A"}],
                    },
                    {
                        "id": "q1",
                        "prompt": "Second q1",
                        "options": [{"id": "b", "label": "B"}],
                    },
                ]
            },
        )

        assert result == {
            "outcome": {
                "outcome": "answered",
                "answers": [{"questionId": "q1", "selectedOptionIds": ["a"]}],
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
        turn = cursor_turn(adapter, "room-1", DecisionTools(), "user-1", "session-1")

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
    async def test_manual_permission_can_be_denied_from_the_room(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        tools = DecisionTools()
        adapter = await resident(cursor_room)
        activate(adapter, cursor_turn(adapter, "room-1", tools, "user-1", "session-1"))
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
        token = pending_tokens(adapter)[0]

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor deny {token}", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )

        assert await pending is None

    @pytest.mark.asyncio
    async def test_manual_permission_can_select_an_advertised_option(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        tools = DecisionTools()
        adapter = await resident(cursor_room)
        activate(adapter, cursor_turn(adapter, "room-1", tools, "user-1", "session-1"))
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
        token = pending_tokens(adapter)[0]

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor select {token} allow-once", sender_id="user-1"
                ),
            ),
            tools,
            "room-1",
        )

        assert await pending == "allow-once"

    @pytest.mark.asyncio
    async def test_an_always_grant_settles_a_parallel_repeat_without_asking(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        tools = DecisionTools()
        adapter = await resident(cursor_room)
        activate(adapter, cursor_turn(adapter, "room-1", tools, "user-1", "session-1"))

        def request(call_id: str) -> ACPPermissionRequest:
            return ACPPermissionRequest(
                room_id="room-1",
                session_id="session-1",
                tool_call=ACPToolCall(call_id, "`echo x >> out.txt`", {}),
                options=(
                    PermissionOption(
                        optionId="allow-once", name="Allow once", kind="allow_once"
                    ),
                    PermissionOption(
                        optionId="allow-always",
                        name="Allow always",
                        kind="allow_always",
                    ),
                ),
            )

        first = asyncio.create_task(adapter._resolve_cursor_permission(request("a")))
        repeat = asyncio.create_task(adapter._resolve_cursor_permission(request("b")))
        await tools.prompt_sent.wait()
        [token] = pending_tokens(adapter)

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor select {token} allow-always", sender_id="user-1"
                ),
            ),
            tools,
            "room-1",
        )

        assert await asyncio.gather(first, repeat) == ["allow-always"] * 2
        assert len(permission_requests(tools)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("plan_mode", "outcome"),
        [("auto_accept", "accepted"), ("auto_decline", "rejected")],
    )
    async def test_automatic_plan_policy_returns_its_outcome(
        self, plan_mode: str, outcome: str
    ) -> None:
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig.model_validate({"plan_mode": plan_mode})
        )

        result = await adapter._resolve_plan(
            cursor_turn(adapter, "room-1", DecisionTools(), "user-1", "session-1"),
            {"plan": "Plan"},
        )

        assert result == {"outcome": {"outcome": outcome}}

    @pytest.mark.asyncio
    async def test_manual_plan_accepts_a_room_command(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._resolve_plan(
                cursor_turn(adapter, "room-1", tools, "user-1", "session-1"),
                {"plan": "Plan"},
            )
        )
        await tools.prompt_sent.wait()
        token = pending_tokens(adapter)[0]
        assert tools.messages == [
            PLAN_REQUESTED_TEMPLATE.format(plan="Plan", token=token)
        ]

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor accept {token}", sender_id="user-1"),
            ),
            tools,
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
            turn=cursor_turn(
                adapter, "room-1", FailingDecisionTools(), "user-1", "session-1"
            ),
            prompt="Plan {token}",
        )

        assert result is None
        assert len(pending_tokens(adapter)) == 0

    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_timeout_notice_failure_still_cancels_the_decision(self) -> None:
        adapter = CursorACPAdapter()

        result = await adapter._wait_for_decision(
            kind="plan",
            turn=cursor_turn(
                adapter,
                "room-1",
                FailingDecisionTools(fail_after=1),
                "user-1",
                "session-1",
            ),
            prompt="Plan {token}",
        )

        assert result is None
        assert len(pending_tokens(adapter)) == 0

    @pytest.mark.asyncio
    async def test_a_reply_that_claims_while_the_prompt_send_fails_still_wins(
        self,
    ) -> None:
        """`/cursor decisions` lists a token before its prompt lands; a reply
        claiming it while that send fails owns the answer."""
        tools = DecisionTools()
        failing_prompt = tools.hold_message("Plan ", error=RuntimeError("network down"))
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=cursor_turn(adapter, "room-1", tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )

        async with failing_prompt:
            token = pending_tokens(adapter)[0]
            await adapter._handle_control_message(
                room_message(f"/cursor accept {token}"), tools, "room-1"
            )

        assert await pending == {"outcome": {"outcome": "accepted"}}
        assert tools.messages == [f"Cursor plan decision `{token}` resolved."]

    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_a_late_reply_during_the_timeout_notice_is_not_reported_as_resolved(
        self,
    ) -> None:
        """A room reply for a token whose timeout notice is still being sent
        must be told "not pending" -- the timeout already cancelled it."""
        tools = DecisionTools()
        timeout_notice = tools.hold_message("timed out")
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=cursor_turn(adapter, "room-1", tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )
        await tools.prompt_sent.wait()
        [token] = pending_tokens(adapter)

        async with timeout_notice:
            await adapter._handle_control_message(
                room_message(f"/cursor accept {token}"), tools, "room-1"
            )
            assert tools.messages[-1] == f"Cursor decision `{token}` is not pending."

        assert await pending is None

    @pytest.mark.asyncio
    async def test_a_reply_for_an_already_claimed_token_is_told_not_pending(
        self,
    ) -> None:
        """The second claim-guard gap the shared registry closes: nothing in
        the original code stopped _handle_control_message from resolving a
        token something else (a timeout, in production) had already claimed
        a moment earlier -- only the timeout-notice-send window above was
        guarded. Simulate that by claiming the token directly before the
        room command arrives: the command must report "not pending", never
        touch the future, and never say "resolved"."""
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        turn = cursor_turn(adapter, "room-1", tools, "user-1", "session-1")

        pending_task = asyncio.create_task(
            adapter._wait_for_decision(kind="plan", turn=turn, prompt="Plan {token}")
        )
        await tools.prompt_sent.wait()
        token = pending_tokens(adapter)[0]

        claimed = adapter._pending_decisions["room-1"].try_claim(token)
        assert claimed is not None

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor accept {token}", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == f"Cursor decision `{token}` is not pending."
        assert not claimed.payload.future.done()

        claimed.payload.future.set_result(None)
        assert await pending_task is None

    @pytest.mark.asyncio
    async def test_room_cleanup_cancels_only_its_pending_decision(self) -> None:
        first_tools = DecisionTools()
        second_tools = DecisionTools()
        adapter = CursorACPAdapter()
        first = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=cursor_turn(adapter, "room-1", first_tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )
        second = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=cursor_turn(
                    adapter, "room-2", second_tools, "user-2", "session-2"
                ),
                prompt="Plan {token}",
            )
        )
        await asyncio.gather(
            first_tools.prompt_sent.wait(), second_tools.prompt_sent.wait()
        )
        tokens = {room: token for room, [token] in pending_decisions(adapter).items()}

        await adapter.on_cleanup("room-1")

        assert await first is None
        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor accept {tokens['room-1']}", sender_id="user-1"
                ),
            ),
            first_tools,
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
            second_tools,
            "room-2",
        )

        assert await second == {"outcome": {"outcome": "accepted"}}

    @pytest.mark.asyncio
    async def test_a_room_reply_resolves_the_decision_it_was_sent_for(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """on_message must return once a decision opens, not after the whole
        turn -- otherwise the very reply meant to resolve it would queue
        behind the still-open turn and every manual decision would time
        out. The room delivers both messages sequentially, as
        ExecutionContext does, to a real ACP turn."""
        room = await cursor_room(
            FakeACPAgent()
            .will_ask_permission(title="shell", options=allow_or_reject("allow-once"))
            .will_call_mcp_tool(
                "reply",
                "band_send_message",
                arguments={"content": "ran it", "mentions": ["@alice"]},
            )
        )

        turn = await room.send("run it")
        [prompt] = said(turn)
        reply = await room.send(f"/cursor select {decision_token(prompt)} allow-once")
        await turn.until_said("ran it")

        assert room.agent.approved is True
        assert said(reply) == [
            f"Cursor permission decision `{decision_token(prompt)}` resolved."
        ]
