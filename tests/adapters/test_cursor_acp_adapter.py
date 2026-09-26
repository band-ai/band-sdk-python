"""Behavioral tests for the Cursor ACP backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from acp.schema import PermissionOption

from band.adapters.cursor_acp import (
    DEFAULT_CURSOR_ACP_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
    CursorTurn,
)
from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPClientAdapter, ACPPermissionRequest
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.session_config import ACPConfigRequest
from band.integrations.acp.types import ACPToolCall
from band.testing import FakeAgentTools


class DecisionTools(FakeAgentTools):
    """Room tools that enforce the real mention contract (unlike a hand-rolled
    fake, this raises on a mention-less send -- see FakeAgentTools.send_message)
    and signal once a pending decision is visible in the room."""

    def __init__(self) -> None:
        super().__init__()
        self.prompt_sent = asyncio.Event()

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        result = await super().send_message(content, mentions)
        self.prompt_sent.set()
        return result

    @property
    def messages(self) -> list[str]:
        return [cast(str, sent["content"]) for sent in self.messages_sent]


class FailingDecisionTools(DecisionTools):
    def __init__(self, *, fail_after: int = 0) -> None:
        super().__init__()
        self._fail_after = fail_after

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        if len(self.messages_sent) >= self._fail_after:
            raise RuntimeError("room delivery failed")
        return await super().send_message(content, mentions)


class SlowSecondSendTools(DecisionTools):
    """Blocks its second ``send_message`` (the timeout notice) until released,
    so a test can inject a room reply for the same token while that notice
    send is still in flight. Only ever gates once, so a reply sent while
    gated (itself a further ``send_message`` call) is not blocked too."""

    def __init__(self) -> None:
        super().__init__()
        self.sending_second_message = asyncio.Event()
        self.release_second_message = asyncio.Event()
        self._gated = False

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        if not self._gated and len(self.messages_sent) == 1:
            self._gated = True
            self.sending_second_message.set()
            await self.release_second_message.wait()
        return await super().send_message(content, mentions)


def _turn(
    room_id: str,
    tools: AgentToolsProtocol,
    requester_id: str | None,
    session_id: str | None = None,
) -> CursorTurn:
    return CursorTurn(
        room_id=room_id,
        tools=tools,
        requester_id=requester_id,
        release=asyncio.get_running_loop().create_future(),
        session_id=session_id,
    )


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

    def test_rejects_an_empty_command(self) -> None:
        with pytest.raises(ValueError, match="command must not be empty"):
            CursorACPAdapter(CursorACPAdapterConfig(command=()))

    def test_rejects_a_non_positive_decision_timeout(self) -> None:
        with pytest.raises(ValueError, match="decision_timeout_s must be greater"):
            CursorACPAdapter(CursorACPAdapterConfig(decision_timeout_s=0.0))

    def test_rejects_a_non_positive_max_pending_decisions(self) -> None:
        with pytest.raises(ValueError, match="max_pending_decisions must be greater"):
            CursorACPAdapter(CursorACPAdapterConfig(max_pending_decisions=0))

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

    def test_rejects_a_decision_timeout_that_does_not_fit_inside_the_turn_timeout(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="decision_timeout_s.*turn_timeout_s"):
            CursorACPAdapter(
                CursorACPAdapterConfig(decision_timeout_s=300.0, turn_timeout_s=300.0)
            )

    def test_turn_timeout_s_is_a_typed_config_field(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(turn_timeout_s=1800.0))

        assert adapter._turn_timeout_s == 1800.0

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
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        turn = _turn("room-1", tools, "user-1", "session-1")
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
        turn = _turn("room-1", tools, "requester", "session-1")
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
            tools,
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
        turn = _turn("room-1", tools, "user-1", "session-1")
        adapter._active_turn = turn

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
        token = next(iter(adapter._pending_decisions))

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
        turn = _turn("room-1", DecisionTools(), "user-1", "session-1")

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
        turn = _turn("room-1", DecisionTools(), "user-1", "session-1")

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
        turn = _turn("room-1", DecisionTools(), "user-1", "session-1")

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
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        adapter._active_turn = _turn("room-1", tools, "user-1", "session-1")
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
            tools,
            "room-1",
        )

        assert await pending is None

    @pytest.mark.asyncio
    async def test_manual_permission_can_select_an_advertised_option(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        adapter._active_turn = _turn("room-1", tools, "user-1", "session-1")
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
            tools,
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
            _turn("room-1", DecisionTools(), "user-1", "session-1"),
            {"plan": "Plan"},
        )

        assert result == {"outcome": {"outcome": outcome}}

    @pytest.mark.asyncio
    async def test_manual_plan_accepts_a_room_command(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._resolve_plan(
                _turn("room-1", tools, "user-1", "session-1"),
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
            turn=_turn("room-1", FailingDecisionTools(), "user-1", "session-1"),
            prompt="Plan {token}",
        )

        assert result is None
        assert len(adapter._pending_decisions) == 0

    @pytest.mark.asyncio
    async def test_timeout_notice_failure_still_cancels_the_decision(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(decision_timeout_s=0.001))

        result = await adapter._wait_for_decision(
            kind="plan",
            turn=_turn(
                "room-1", FailingDecisionTools(fail_after=1), "user-1", "session-1"
            ),
            prompt="Plan {token}",
        )

        assert result is None
        assert len(adapter._pending_decisions) == 0

    @pytest.mark.asyncio
    async def test_a_reply_that_claims_while_the_prompt_send_fails_still_wins(
        self,
    ) -> None:
        """`/cursor decisions` lists a token before its prompt lands; a reply
        claiming it while that send fails owns the answer."""
        prompt_in_flight, fail_prompt = asyncio.Event(), asyncio.Event()

        class FailingPromptTools(DecisionTools):
            async def send_message(
                self,
                content: str,
                mentions: list[str] | list[dict[str, str]] | None = None,
            ) -> object:
                if content.startswith("Plan "):
                    prompt_in_flight.set()
                    await fail_prompt.wait()
                    raise RuntimeError("network down")
                return await super().send_message(content, mentions)

        tools = FailingPromptTools()
        adapter = CursorACPAdapter()
        turn = _turn("room-1", tools, "user-1", "session-1")
        pending = asyncio.create_task(
            adapter._wait_for_decision(kind="plan", turn=turn, prompt="Plan {token}")
        )
        await asyncio.wait_for(prompt_in_flight.wait(), 1)
        token = next(iter(adapter._pending_decisions))

        await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor accept {token}", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )
        fail_prompt.set()

        assert await asyncio.wait_for(pending, 1) == {
            "outcome": {"outcome": "accepted"}
        }
        assert tools.messages == [f"Cursor plan decision `{token}` resolved."]

    @pytest.mark.asyncio
    async def test_a_late_reply_during_the_timeout_notice_is_not_reported_as_resolved(
        self,
    ) -> None:
        """Regression: a decision's token stayed in `_pending_decisions` while
        the (awaited) timeout notice was still being sent, so a room reply
        for that same token landing in that window found it still pending
        and was told 'resolved' -- even though the timeout had already
        cancelled the future and the reply was discarded."""
        tools = SlowSecondSendTools()
        adapter = CursorACPAdapter(CursorACPAdapterConfig(decision_timeout_s=0.01))
        turn = _turn("room-1", tools, "user-1", "session-1")

        pending = asyncio.create_task(
            adapter._wait_for_decision(kind="plan", turn=turn, prompt="Plan {token}")
        )
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        # The decision has timed out and the notice send is in flight.
        await tools.sending_second_message.wait()
        messages_before = len(tools.messages)
        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content=f"/cursor accept {token}", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )
        # The reply _handle_control_message just sent -- not necessarily the
        # last message overall, since the still-in-flight notice send (below)
        # completes after this reply does.
        reply = tools.messages[messages_before]
        tools.release_second_message.set()

        assert await pending is None
        assert handled is True
        assert reply == f"Cursor decision `{token}` is not pending."

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
        turn = _turn("room-1", tools, "user-1", "session-1")

        pending_task = asyncio.create_task(
            adapter._wait_for_decision(kind="plan", turn=turn, prompt="Plan {token}")
        )
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        claimed = adapter._pending_decisions.try_claim(token)
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
                turn=_turn("room-1", first_tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )
        second = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=_turn("room-2", second_tools, "user-2", "session-2"),
                prompt="Plan {token}",
            )
        )
        await asyncio.gather(
            first_tools.prompt_sent.wait(), second_tools.prompt_sent.wait()
        )
        tokens = {
            entry.payload.room_id: entry.token
            for entry in adapter._pending_decisions.unclaimed()
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
    async def test_a_room_reply_resolves_the_decision_it_was_sent_for(self) -> None:
        """Regression: on_message must return once a decision opens, not
        after the whole turn completes -- otherwise the very reply meant to
        resolve a pending decision can never be processed (it would queue
        behind the same still-open on_message call), and every manual
        decision degrades to a timeout. This drives two on_message calls the
        way ExecutionContext would: sequentially, the second only after the
        first has returned."""
        tools = DecisionTools()
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(decision_timeout_s=5.0, turn_timeout_s=10.0)
        )
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
        decided: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

        async def fake_acp_turn(*args: object, **kwargs: object) -> None:
            del args, kwargs
            # super().on_message() is stubbed out, so it never runs
            # _get_or_create_session -- bind the session the same way that
            # would, so _active_turn_for below can match this turn.
            assert adapter._active_turn is not None
            adapter._active_turn.session_id = request.session_id
            decided.set_result(await adapter._resolve_cursor_permission(request))

        with patch.object(ACPClientAdapter, "on_message", fake_acp_turn):
            run_message = cast(
                PlatformMessage, SimpleNamespace(content="run it", sender_id="user-1")
            )
            await asyncio.wait_for(
                adapter.on_message(
                    run_message,
                    tools,
                    cast(ACPClientSessionState, None),
                    None,
                    None,
                    is_session_bootstrap=True,
                    room_id="room-1",
                ),
                timeout=1.0,
            )
            # on_message already returned, well inside decision_timeout_s --
            # the decision opened and released it rather than blocking for
            # the whole (still in-flight) turn.
            assert not decided.done()
            token = next(iter(adapter._pending_decisions))

            reply_message = cast(
                PlatformMessage,
                SimpleNamespace(
                    content=f"/cursor select {token} allow-once", sender_id="user-1"
                ),
            )
            await asyncio.wait_for(
                adapter.on_message(
                    reply_message,
                    tools,
                    cast(ACPClientSessionState, None),
                    None,
                    None,
                    is_session_bootstrap=False,
                    room_id="room-1",
                ),
                timeout=1.0,
            )

        assert await asyncio.wait_for(decided, timeout=1.0) == "allow-once"


class TestCursorACPAdapterControlMessages:
    @pytest.mark.asyncio
    async def test_bare_cursor_lists_pending_decisions(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage, SimpleNamespace(content="/cursor", sender_id="user-1")
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == "Pending Cursor decisions: none"

    @pytest.mark.asyncio
    async def test_cursor_decisions_lists_pending_decisions(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content="/cursor decisions", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == "Pending Cursor decisions: none"

    @pytest.mark.asyncio
    async def test_a_two_word_command_shows_usage(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content="/cursor accept", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == (
            "Use `/cursor decisions` to list pending Cursor decisions."
        )

    @pytest.mark.asyncio
    async def test_a_structurally_invalid_decision_command_is_rejected(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._resolve_plan(
                _turn("room-1", tools, "user-1", "session-1"),
                {"plan": "Plan"},
            )
        )
        await tools.prompt_sent.wait()
        token = next(iter(adapter._pending_decisions))

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                # "select" is a permission verb, not a plan verb.
                SimpleNamespace(
                    content=f"/cursor select {token} allow-once", sender_id="user-1"
                ),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == (
            f"That command is not valid for Cursor plan decision `{token}`."
        )
        assert not pending.done()
        adapter._cancel_all_decisions()
        await pending

    @pytest.mark.asyncio
    async def test_manual_permission_denies_silently_with_no_matching_active_turn(
        self,
    ) -> None:
        """No active turn for this room/session means there is no room to
        relay the decision to; it must deny without sending anything."""
        adapter = CursorACPAdapter()
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

        result = await adapter._resolve_cursor_permission(request)

        assert result is None

    @pytest.mark.asyncio
    async def test_exceeding_max_pending_decisions_evicts_the_oldest(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(max_pending_decisions=1))
        first_tools = DecisionTools()
        second_tools = DecisionTools()

        first = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=_turn("room-1", first_tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )
        await first_tools.prompt_sent.wait()
        assert len(adapter._pending_decisions) == 1

        second = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=_turn("room-2", second_tools, "user-2", "session-2"),
                prompt="Plan {token}",
            )
        )
        await second_tools.prompt_sent.wait()

        assert await first is None
        assert len(adapter._pending_decisions) == 1
        adapter._cancel_all_decisions()
        await second
