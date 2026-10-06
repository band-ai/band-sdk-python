"""Behavioral tests for the Cursor ACP backend."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from acp.helpers import start_tool_call, update_tool_call
from acp.schema import PermissionOption
from pydantic import BaseModel, create_model

from band.adapters.cursor_acp import (
    DECISION_UNAUTHORIZED_MESSAGE,
    DEFAULT_CURSOR_ACP_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
    CursorTurn,
)
from band.client.streaming import ControlMode
from band.core.protocols import AgentToolsProtocol
from band.core.types import AgentInput, Capability, HistoryProvider, PlatformMessage
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.cursor import PLAN_REQUESTED_TEMPLATE
from band.integrations.acp.types import ACPToolCall
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME, mcp_tool_spelling
from band.runtime.tools.types import BandTool
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    AcpSession,
    launch_for,
    pair_in_process,
    started_acp_adapter,
)
from tests.mcpclient import STORE_MEMORY_ARGS, crash_backend


class DecisionTools(FakeAgentTools):
    """Room tools that enforce the real mention contract (unlike a hand-rolled
    fake, this raises on a mention-less send -- see FakeAgentTools.send_notice)
    and signal once a pending decision is visible in the room.

    Decision prompts and replies are the adapter's own posts, so they go
    through ``send_notice``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.prompt_sent = asyncio.Event()

    async def send_notice(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        result = await super().send_notice(content, mentions)
        self.prompt_sent.set()
        return result

    @property
    def messages(self) -> list[str]:
        return [cast(str, sent["content"]) for sent in self.messages_sent]


class FailingDecisionTools(DecisionTools):
    def __init__(self, *, fail_after: int = 0) -> None:
        super().__init__()
        self._fail_after = fail_after

    async def send_notice(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        if len(self.messages_sent) >= self._fail_after:
            raise RuntimeError("room delivery failed")
        return await super().send_notice(content, mentions)


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


def room_message(
    content: str, *, room_id: str = "room-1", sender_id: str = "user-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def decision_token(prompt: str) -> str:
    """The token a decision prompt asks the room to answer with."""
    match = re.search(r"`/cursor \w+ (\S+)", prompt)
    assert match is not None, prompt
    return match[1]


def said(tools: FakeAgentTools) -> list[str]:
    return [cast(str, sent["content"]) for sent in tools.messages_sent]


def permission_requests(tools: FakeAgentTools) -> list[str]:
    return [message for message in said(tools) if "needs permission" in message]


class CursorRoom:
    """A Cursor room whose ``agent acp`` peer is a scripted in-process ACP
    agent: each message goes through ``on_event`` with its own tools, as the
    runtime delivers it, so every turn is judged."""

    def __init__(self, adapter: CursorACPAdapter, agent: FakeACPAgent) -> None:
        self.adapter = adapter
        self.agent = agent
        self._bootstrapped = False

    async def send(self, content: str) -> FakeAgentTools:
        """Deliver ``content``; return the tools its turn posted through."""
        tools = FakeAgentTools(room_id="room-1")
        bootstrap, self._bootstrapped = not self._bootstrapped, True
        await self.adapter.on_event(
            AgentInput(
                msg=room_message(content),
                tools=tools,
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=bootstrap,
                room_id="room-1",
            )
        )
        return tools

    async def turns_finished(self) -> None:
        """Wait for every turn still running after its message returned."""
        await asyncio.gather(*self.adapter._background_tasks, return_exceptions=True)


# The adapter's ACP runtime lives on the loop that started it, so setup, the
# test and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def cursor_room() -> AsyncIterator[Callable[..., Awaitable[CursorRoom]]]:
    adapters: list[CursorACPAdapter] = []

    async def open_room(
        agent: FakeACPAgent,
        *,
        capabilities: set[Capability] | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **config: Any,
    ) -> CursorRoom:
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(**config),
            capabilities=capabilities,
            additional_tools=additional_tools,
        )
        pair_in_process(adapter, agent)
        await adapter.on_started("Cursor", "Cursor agent under test")
        adapters.append(adapter)
        return CursorRoom(adapter, agent)

    yield open_room
    for adapter in adapters:
        await adapter.stop()


def cursor_in(
    workspace_root: Path, config: CursorACPAdapterConfig | None = None
) -> CursorACPAdapter:
    """A Cursor adapter whose room workspaces live under ``workspace_root``."""
    return CursorACPAdapter(
        config, workspace_for_room=lambda room_id: str(workspace_root / room_id)
    )


class EchoInput(BaseModel):
    text: str


def echo(arguments: EchoInput) -> str:
    return arguments.text


def custom_reply() -> str:
    return "custom reply"


def cursor_custom_tools(
    echo_handler: Callable[[EchoInput], str],
) -> list[CustomToolDef]:
    return [
        (EchoInput, echo_handler),
        (
            create_model(
                f"{mcp_tool_spelling(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)}Input"
            ),
            custom_reply,
        ),
    ]


@pytest.fixture
def cursor_approval_room(
    cursor_room: Callable[..., Awaitable[CursorRoom]],
) -> Callable[[str, str], Awaitable[CursorRoom]]:
    async def open_room(title: str, approval_mode: str) -> CursorRoom:
        return await cursor_room(
            FakeACPAgent().will_ask_permission(title=title).will_say("finished"),
            approval_mode=approval_mode,
            capabilities={Capability.MEMORY},
            additional_tools=cursor_custom_tools(echo),
            decision_timeout_s=0.1,
        )

    return open_room


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_mode", ["manual", "auto_accept", "auto_decline"])
@pytest.mark.parametrize(
    "title",
    [
        pytest.param("band-band_store_memory: band_store_memory", id="memory"),
        pytest.param(
            "band-band_send_message: band_send_message",
            id="reply-with-custom-name-collision",
        ),
        pytest.param("band-echo: echo", id="custom-tool"),
    ],
)
async def test_registered_band_tools_are_approved_without_asking_the_room(
    cursor_approval_room: Callable[[str, str], Awaitable[CursorRoom]],
    approval_mode: str,
    title: str,
) -> None:
    room = await cursor_approval_room(title, approval_mode)
    tools = await room.send("run the tool")
    await room.turns_finished()

    assert room.agent.approved is True
    assert permission_requests(tools) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_mode", "approved", "room_requests"),
    [
        ("manual", False, 1),
        ("auto_accept", True, 0),
        ("auto_decline", False, 0),
    ],
)
@pytest.mark.parametrize(
    "title",
    [
        pytest.param("other-band_send_message: band_send_message", id="other-server"),
        pytest.param(
            "band-band_store_memory: band_send_message", id="mismatched-identity"
        ),
        pytest.param("shell", id="native-tool"),
        pytest.param("shell: shell", id="native-display-title"),
        pytest.param("band-unknown: unknown", id="unregistered-tool"),
    ],
)
async def test_other_tools_follow_cursor_approval_policy(
    cursor_approval_room: Callable[[str, str], Awaitable[CursorRoom]],
    approval_mode: str,
    title: str,
    approved: bool,
    room_requests: int,
) -> None:
    room = await cursor_approval_room(title, approval_mode)
    tools = await room.send("run the tool")
    await room.turns_finished()

    assert room.agent.approved is approved
    assert len(permission_requests(tools)) == room_requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_mode", "first_edit", "second_edit"),
    [
        ("manual", False, True),
        ("auto_accept", True, True),
        ("auto_decline", False, False),
    ],
)
async def test_project_workflow_keeps_band_tools_available_across_human_gates_and_rejoin(
    cursor_room: Callable[..., Awaitable[CursorRoom]],
    tmp_path: Path,
    approval_mode: str,
    first_edit: bool,
    second_edit: bool,
) -> None:
    project = tmp_path / "project.txt"
    project.write_text("original")
    audit = tmp_path / "audit.txt"
    marker = "project workflow completed"

    def record_work(arguments: EchoInput) -> str:
        with audit.open("a") as stream:
            stream.write(arguments.text + "\n")
        return arguments.text

    async def edit_project(agent: FakeACPAgent, session_id: str) -> None:
        await agent.emit(session_id, start_tool_call("native-edit", "shell"))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])",
            str(project),
            "repaired",
        )
        if await process.wait() != 0:
            raise RuntimeError("project edit failed")
        await agent.emit(
            session_id, update_tool_call("native-edit", raw_output="project repaired")
        )

    agent = (
        FakeACPAgent()
        .will_ask_permission(title="band-echo: echo")
        .will_call_mcp_tool(
            "custom-work", "echo", arguments={"text": marker}, requires_approval=True
        )
    )
    agent.will_ask_permission(title="shell").will_execute_if_approved(edit_project)
    for tool, arguments in [
        (BandTool.STORE_MEMORY, {**STORE_MEMORY_ARGS, "content": marker}),
        (BandTool.SEND_MESSAGE, {"content": marker, "mentions": ["@user-1"]}),
    ]:
        agent.will_ask_permission(
            title=f"{mcp_tool_spelling(BAND_MCP_SERVER_NAME, tool)}: {tool}"
        ).will_call_mcp_tool(tool, tool, arguments=arguments, requires_approval=True)
    agent.will_say("I already posted the result.")
    room = await cursor_room(
        agent,
        approval_mode=approval_mode,
        capabilities={Capability.MEMORY},
        additional_tools=cursor_custom_tools(record_work),
    )

    for attempt, edited in enumerate((first_edit, second_edit)):
        project.write_text("original")
        turn = await room.send("repair the project and record the result")
        if approval_mode == "manual":
            assert project.read_text() == "original"
            [request] = permission_requests(turn)
            assert "shell" in request
            command = "deny" if attempt == 0 else "select"
            option = "" if attempt == 0 else " allow-1"
            decision = await room.send(
                f"/cursor {command} {decision_token(request)}{option}"
            )
            assert failure_reports(decision) == []
        await room.turns_finished()

        assert project.read_text() == ("repaired" if edited else "original")
        assert audit.read_text().splitlines() == [marker] * (attempt + 1)
        assert turn.memory_contents == [marker]
        assert said(turn) == [*permission_requests(turn), marker]
        assert turn.turn.replied
        assert failure_reports(turn) == []
        await room.adapter.on_cleanup("room-1")


class TestCursorACPAdapterConfig:
    @pytest.mark.parametrize(
        ("settings", "error"),
        [
            ({"api_key": "a", "auth_token": "b"}, "either api_key or auth_token"),
            ({"command": ()}, "requires a command"),
            (
                {"auth_method": "api_key"},
                "auth_method\n  Input should be 'cursor_login'",
            ),
            (
                {"decision_timeout_s": 0.0},
                "decision_timeout_s\n  Input should be greater than 0",
            ),
            (
                {"max_pending_decisions": 0},
                "max_pending_decisions\n  Input should be greater than 0",
            ),
            (
                {"decision_timeout_s": 300.0, "turn_timeout_s": 300.0},
                "decision_timeout_s must be less than turn_timeout_s",
            ),
            (
                {"decision_timeout_s": 900.0},
                "decision_timeout_s must be less than turn_timeout_s",
            ),
        ],
        ids=[
            "ambiguous-auth",
            "empty-command",
            "foreign-auth-method",
            "non-positive-decision-timeout",
            "non-positive-max-pending",
            "decision-timeout-equals-turn-timeout",
            "decision-timeout-reaching-the-default-turn-timeout",
        ],
    )
    def test_rejects_invalid_settings(
        self, settings: dict[str, object], error: str
    ) -> None:
        with pytest.raises(ValueError, match=error):
            CursorACPAdapterConfig.model_validate(settings)

    def test_authorized_senders_load_from_any_list_of_ids(self) -> None:
        config = CursorACPAdapterConfig.model_validate(
            {"decision_authorized_senders": ["owner", "owner", "admin"]}
        )

        assert config.decision_authorized_senders == frozenset({"owner", "admin"})

    def test_cwd_and_a_workspace_resolver_are_exclusive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="either cwd or workspace_for_room"):
            CursorACPAdapter(
                CursorACPAdapterConfig(cwd=str(tmp_path)),
                workspace_for_room=lambda room_id: str(tmp_path / room_id),
            )


class TestCursorACPAdapterLaunch:
    @pytest.mark.asyncio
    async def test_launches_agent_acp_without_authenticate(
        self, tmp_path: Path
    ) -> None:
        adapter = cursor_in(tmp_path)

        launch = await launch_for(adapter)

        assert launch.command == DEFAULT_CURSOR_ACP_COMMAND
        assert launch.auth_method is None
        assert adapter._profile is adapter._cursor_profile

    @pytest.mark.parametrize(
        ("config", "env"),
        [
            pytest.param(
                CursorACPAdapterConfig(api_key="key"),
                {"CURSOR_API_KEY": "key"},
                id="api-key",
            ),
            pytest.param(
                CursorACPAdapterConfig(auth_token="token", env={"OTHER": "x"}),
                {"OTHER": "x", "CURSOR_AUTH_TOKEN": "token"},
                id="auth-token-merged-into-env",
            ),
            pytest.param(
                CursorACPAdapterConfig(
                    api_key="shortcut", env={"CURSOR_API_KEY": "environment"}
                ),
                {"CURSOR_API_KEY": "environment"},
                id="env-wins-over-api-key",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_credentials_reach_the_agent_environment(
        self, config: CursorACPAdapterConfig, env: dict[str, str], tmp_path: Path
    ) -> None:
        launch = await launch_for(cursor_in(tmp_path, config))

        assert launch.env == env

    @pytest.mark.asyncio
    async def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter, "room-a")

        assert launch.cwd == str(tmp_path / "room-a")


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
    async def test_an_always_grant_settles_a_parallel_repeat_without_asking(
        self,
    ) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        adapter._active_turn = _turn("room-1", tools, "user-1", "session-1")

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
        [token] = adapter._pending_decisions

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
            turn=_turn("room-1", FailingDecisionTools(), "user-1", "session-1"),
            prompt="Plan {token}",
        )

        assert result is None
        assert len(adapter._pending_decisions) == 0

    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_timeout_notice_failure_still_cancels_the_decision(self) -> None:
        adapter = CursorACPAdapter()

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
        tools = DecisionTools()
        failing_prompt = tools.hold_message("Plan ", error=RuntimeError("network down"))
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=_turn("room-1", tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )

        async with failing_prompt:
            token = next(iter(adapter._pending_decisions))
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
                turn=_turn("room-1", tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )
        await tools.prompt_sent.wait()
        [token] = [entry.token for entry in adapter._pending_decisions.unclaimed()]

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
            .will_ask_permission(title="shell", allow_option_id="allow-once")
            .will_say("ran it")
        )

        turn = await room.send("run it")
        [prompt] = said(turn)
        reply = await room.send(f"/cursor select {decision_token(prompt)} allow-once")
        await turn.until_said("ran it")

        assert room.agent.approved is True
        assert said(reply) == [
            f"Cursor permission decision `{decision_token(prompt)}` resolved."
        ]


class TestCursorACPAdapterDetachedTurn:
    """A turn parked on a decision releases its message early, so it is
    judged at its real end instead of when ``on_event`` returns."""

    @pytest.mark.asyncio
    async def test_a_detached_turn_that_ends_with_nothing_is_reported_after_release(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", allow_option_id="allow-once"
            )
        )

        # on_event returning normally is what keeps the delivery PROCESSED.
        turn = await room.send("run it")
        [prompt] = said(turn)
        assert failure_reports(turn) == []

        await room.send(f"/cursor deny {decision_token(prompt)}")
        await room.turns_finished()

        assert failure_reports(turn) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_a_decision_reply_settles_on_its_own_tools(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """A decision reply is a whole turn of its own: its notice settles
        that message's tools, and neither it nor the decision prompt stands
        in for the parked turn's answer, which still relays there."""
        room = await cursor_room(
            FakeACPAgent()
            .will_ask_permission(title="shell", allow_option_id="allow-once")
            .will_say("ran it")
        )
        turn = await room.send("run it")
        [prompt] = said(turn)

        reply = await room.send(f"/cursor select {decision_token(prompt)} allow-once")
        await room.turns_finished()

        assert reply.turn.complete
        assert not reply.turn.replied
        assert failure_reports(reply) == []
        assert said(turn) == [prompt, "ran it"]
        assert failure_reports(turn) == []

    @pytest.mark.asyncio
    async def test_a_turn_cancelled_by_cleanup_is_not_judged(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", allow_option_id="allow-once"
            )
        )
        turn = await room.send("run it")

        await room.adapter.on_cleanup("room-1")
        await room.turns_finished()

        # The stopped connection fails the turn with the ACP error it raised.
        assert MISSING_REPLY_FAILURE not in failure_reports(turn)

    @pytest.mark.asyncio
    async def test_an_interrupted_detached_turn_is_not_reported(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """A room /stop ends the parked turn; the adapter settled it."""
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", allow_option_id="allow-once"
            )
        )
        turn = await room.send("run it")

        await room.adapter.on_interrupt("room-1", ControlMode.STOP)
        await room.turns_finished()

        assert failure_reports(turn) == []

    @pytest.mark.asyncio
    async def test_a_cancelled_detached_turn_posts_nothing(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", allow_option_id="allow-once"
            )
        )
        turn = await room.send("run it")
        [prompt] = said(turn)

        for task in room.adapter._background_tasks:
            task.cancel()
        await room.turns_finished()

        assert said(turn) == [prompt]
        assert failure_reports(turn) == []


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


async def leave_the_room(adapter: CursorACPAdapter, session: AcpSession) -> None:
    await adapter.on_cleanup("room-1")


async def replace_its_band_server(
    adapter: CursorACPAdapter, session: AcpSession
) -> None:
    await crash_backend(adapter._mcp)
    await session.send("after the crash", room="room-1")
    await adapter._drain_background_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("release", [leave_the_room, replace_its_band_server])
async def test_a_released_sessions_todos_are_forgotten(
    release: Callable[[CursorACPAdapter, AcpSession], Awaitable[None]],
) -> None:
    agent = FakeACPAgent().will_update_cursor_todos("ship it").will_say("ok")
    adapter = CursorACPAdapter(
        CursorACPAdapterConfig(command="fake-agent", inject_band_tools=True)
    )

    async with started_acp_adapter(adapter, agent) as session:
        await session.send("plan it", room="room-1")
        released = session.session_id("room-1")
        await release(adapter, session)

        assert released not in adapter._cursor_profile._todos_by_session


@pytest.mark.asyncio
async def test_a_turn_still_running_at_cleanup_leaves_no_todos() -> None:
    """A turn left running detached keeps updating todos until the runtime's
    stop closes its connection; none of that may outlive the cleanup."""
    agent = FakeACPAgent()
    updating = agent.keeps_updating_cursor_todos()
    adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))

    async with started_acp_adapter(adapter, agent) as session:
        turn = asyncio.create_task(session.send("plan it", room="room-1"))
        await updating.wait()
        released = session.session_id("room-1")
        await adapter.on_cleanup("room-1")

        assert released not in adapter._cursor_profile._todos_by_session
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_cleanup_cancelled_mid_stop_still_releases_the_session() -> None:
    """The room may rejoin and restore its session while the old runtime is
    still exiting, so the bootstrap mark goes before the stop; the todos go
    after it, even when the stop is cancelled."""
    agent = FakeACPAgent().will_update_cursor_todos("ship it").will_say("ok")
    exiting = agent.exits_slowly()
    adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))

    async with started_acp_adapter(adapter, agent) as session:
        await session.send("plan it", room="room-1")
        released = session.session_id("room-1")
        cleanup = asyncio.create_task(adapter.on_cleanup("room-1"))
        await exiting.received.wait()

        assert adapter._claim_session_bootstrap(released)
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        assert released not in adapter._cursor_profile._todos_by_session
