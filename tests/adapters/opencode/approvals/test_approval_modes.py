"""Each approval mode answers an ask the way its policy says."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import Capability
from band.integrations.opencode.types import (
    OpencodeSessionState,
)
from band.testing import FakeAgentTools
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.approvals.kit import (
    DECLINED,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    AskFactory,
    FakeOpencodeClient,
    declined_questions,
    event_message_updated,
    event_permission,
    event_question,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
    time_passes,
    tools_protocol,
)


class TestApprovalModes:
    @pytest.mark.looptime
    @pytest.mark.parametrize(
        ("config", "room_reply", "sent"),
        [
            pytest.param(
                OpencodeAdapterConfig(),
                "approve req-1",
                {"permission_id": "req-1", "reply": "once"},
                id="approve",
            ),
            pytest.param(
                OpencodeAdapterConfig(), "reject req-1", DECLINED, id="reject"
            ),
            pytest.param(
                OpencodeAdapterConfig(approval_mode="auto_decline"),
                None,
                DECLINED,
                id="auto-decline",
            ),
            pytest.param(
                OpencodeAdapterConfig(approval_wait_timeout_s=ASK_DEADLINE_S),
                None,
                DECLINED,
                id="timeout",
            ),
        ],
    )
    async def test_every_decline_tells_the_model_why(
        self,
        asks: AskFactory,
        config: OpencodeAdapterConfig,
        room_reply: str | None,
        sent: dict[str, str],
    ) -> None:
        """OpenCode ends the turn on a bare reject, so every decline carries the
        feedback that hands the turn back to the model to answer the user."""
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client, config=config)

        await approvals.on_permission_asked(asks.permission("req-1"))
        if room_reply is not None:
            assert await approvals.try_handle_reply(room_reply, "user-1")
        await time_passes(ASK_DEADLINE_S + 1)

        assert client.permission_replies == [sent]

    async def test_auto_accept_approval_mode(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-1")],
            ],
            reply_permission_events={
                "perm-1": [
                    event_message_updated("sess-1", "msg-auto"),
                    event_text_part("sess-1", "msg-auto", "auto accepted"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="auto_accept"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.permission_replies == [
            {"permission_id": "perm-1", "reply": "once"}
        ]
        # No approval prompt sent to user in auto_accept mode
        assert not any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )

    async def test_auto_decline_approval_mode(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-1")],
            ],
            reply_permission_events={"perm-1": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="auto_decline"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.permission_replies == [
            {**DECLINED, "permission_id": "perm-1"}
        ]

    async def test_auto_reject_question_mode(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_question("sess-1", "q-1", "What to do?")]],
            reply_question_events={"q-1": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(question_mode="auto_reject"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert declined_questions(fake_client) == ["q-1"]
        assert not any(
            "asked question" in m["content"].lower() for m in tools.messages_sent
        )

    async def test_band_tool_permission_auto_approved_in_manual_mode(
        self, make_adapter, tools
    ) -> None:
        """A permission ask naming the adapter's own band tool is granted
        `always` without any room prompt, even in manual mode -- platform
        plumbing must never stall on a human approval."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_permission(
                        "sess-1", "perm-band", permission="band_send_message"
                    )
                ]
            ],
            reply_permission_events={
                "perm-band": [
                    event_message_updated("sess-1", "msg-band"),
                    event_text_part("sess-1", "msg-band", "tool ran"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await run_single_turn(adapter, tools)

        assert fake_client.permission_replies == [
            {
                "permission_id": "perm-band",
                "reply": "always",
            }
        ]
        assert not any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )
        assert any(msg["content"] == "tool ran" for msg in tools.messages_sent)

    async def test_band_tool_permission_matches_server_prefixed_custom_tool(
        self, make_adapter, tools
    ) -> None:
        """OpenCode may report an MCP tool ask under its `{server}_{tool}`
        naming; a server-prefixed custom tool still auto-approves."""

        class EchoInput(BaseModel):
            """Echo text."""

            text: str

        def echo_tool(input_data: EchoInput) -> str:
            return input_data.text

        fake_client = FakeOpencodeClient(
            reply_permission_events={"perm-echo": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="manual"),
            additional_tools=[(EchoInput, echo_tool)],
            client_factory=lambda _config: fake_client,
        )
        await adapter.on_started("OpenCode Agent", "A coding agent")
        # OpenCode prefixes MCP tools with the agent-scoped server name, so the ask
        # names the custom tool as ``{server}_echo``.
        fake_client._prompt_event_sequences = [
            [
                event_permission(
                    "sess-1", "perm-echo", permission=f"{adapter._mcp_server_name}_echo"
                )
            ]
        ]
        tools = FakeAgentTools()

        await run_single_turn(adapter, tools)

        assert fake_client.permission_replies == [
            {
                "permission_id": "perm-echo",
                "reply": "always",
            }
        ]

    async def test_band_tool_permission_bypasses_auto_decline(self) -> None:
        """auto_decline rejects ordinary asks, but the adapter's own band
        tools are still granted -- declining band_store_memory would break
        the platform plumbing the adapter itself registered."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-mem", permission="band_store_memory")]
            ],
            reply_permission_events={"perm-mem": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="auto_decline"),
            capabilities=Capability.MEMORY,
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await run_single_turn(adapter, tools)

        assert fake_client.permission_replies == [
            {
                "permission_id": "perm-mem",
                "reply": "always",
            }
        ]

    async def test_doom_loop_permission_auto_accepted_in_auto_accept_mode(
        self, make_adapter, tools
    ) -> None:
        """Pins the E2E-lane behavior: a non-tool ask (doom_loop) under
        auto_accept is granted `once` -- the safety heuristic keeps firing
        server-side, each trip is just answered without a room prompt."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-loop", permission="doom_loop")]
            ],
            reply_permission_events={"perm-loop": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="auto_accept"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await run_single_turn(adapter, tools)

        assert fake_client.permission_replies == [
            {
                "permission_id": "perm-loop",
                "reply": "once",
            }
        ]
        assert not any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )

    async def test_doom_loop_permission_still_relayed_in_manual_mode(
        self, make_adapter, tools
    ) -> None:
        """Guards the interactive path: non-band asks keep the manual relay
        (room prompt + reply flow), only the adapter's own tools bypass it."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-loop", permission="doom_loop")]
            ],
            reply_permission_events={
                "perm-loop": [
                    event_message_updated("sess-1", "msg-loop"),
                    event_text_part("sess-1", "msg-loop", "continued"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await tools.until(
            lambda: any(
                "approval requested for `doom_loop`" in m["content"].lower()
                for m in tools.messages_sent
            )
        )
        await asyncio.wait([first_turn])
        assert fake_client.permission_replies == []

        await deliver_message(
            adapter,
            make_platform_message(content="approve perm-loop"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await first_turn

        assert fake_client.permission_replies == [
            {
                "permission_id": "perm-loop",
                "reply": "once",
            }
        ]
