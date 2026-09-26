"""A scripted Claude CLI behind the SDK's public ``Transport`` seam.

Everything above the subprocess runs for real: ``ClaudeSDKClient``, its
control protocol, the adapter's ``can_use_tool`` and hooks, and the in-process
Band MCP server. Each prompt plays the next scripted turn, and tool calls pass
the CLI's permission order before they run
(https://code.claude.com/docs/en/agent-sdk/permissions).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError
from claude_agent_sdk._internal.transport import Transport

from tests.baseline.decisions import ModelDecision, ToolCall

MODEL = "claude-fake"
# acceptEdits auto-approves these file-writing tools.
EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
MCP_PROTOCOL_VERSION = "2025-06-18"


@dataclass(frozen=True)
class Raw:
    """A wire message the CLI emits verbatim, for shapes no decision produces."""

    message: dict[str, Any]


@dataclass(frozen=True)
class Thinking:
    """An extended-thinking block."""

    text: str


@dataclass(frozen=True)
class EndTurn:
    """The turn's terminal result, overriding the default success."""

    is_error: bool = False
    result: str | None = None
    errors: list[str] | None = None
    api_error_status: int | None = None


@dataclass(frozen=True)
class Hangup:
    """The CLI process dies: the stream ends with no result."""


@dataclass
class Hold:
    """Parks the turn at this step: ``async with hold`` waits for the turn to
    reach it and lets the turn continue on exit."""

    reached: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    async def __aenter__(self) -> None:
        await self.reached.wait()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.released.set()


Step = ModelDecision | Thinking | Raw | Hold | EndTurn | Hangup
Turn = Sequence[Step]


class FakeClaude:
    """The Claude CLI as every ``ClaudeSDKClient`` in a test sees it."""

    def __init__(self) -> None:
        self._turns: deque[Turn] = deque()
        self._session_ids = itertools.count(1)
        self.sessions: list[FakeCLISession] = []
        self.prompts: list[str] = []
        self.unresumable: set[str] = set()
        # permissions.ask rules in the project's settings file, which the CLI
        # only reads when the options load the "project" setting source.
        self.project_ask_rules: list[str] = []
        self.refuse_connect = False
        self.errors: list[BaseException] = []

    def script(self, *turns: Turn) -> None:
        self._turns.extend(turns)

    def client(self, *, options: ClaudeAgentOptions) -> ClaudeSDKClient:
        """Stands in for the ``ClaudeSDKClient`` constructor."""
        session = FakeCLISession(self, options)
        self.sessions.append(session)
        return ClaudeSDKClient(options=options, transport=session)

    @property
    def resumed(self) -> list[str | None]:
        return [session.options.resume for session in self.sessions]

    def next_turn(self) -> Turn:
        if not self._turns:
            error = AssertionError("The CLI received a prompt no turn was scripted for")
            self.errors.append(error)
            raise error
        return self._turns.popleft()

    def new_session_id(self) -> str:
        return f"sess-{next(self._session_ids)}"

    def assert_done(self) -> None:
        if self.errors:
            raise self.errors[0]
        assert not self._turns, f"{len(self._turns)} scripted turn(s) never ran"


class FakeCLISession(Transport):
    """One CLI process: the CLI side of the stream-json control protocol."""

    def __init__(self, claude: FakeClaude, options: ClaudeAgentOptions) -> None:
        self.claude = claude
        self.options = options
        self.session_id = options.resume or claude.new_session_id()
        self._outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._awaiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._hooks: dict[str, list[dict[str, Any]]] = {}
        self._served: dict[str, set[str]] = {}
        self._turn: asyncio.Task[None] | None = None
        self.alive = True

    async def connect(self) -> None:
        if self.claude.refuse_connect or self.options.resume in self.claude.unresumable:
            raise CLIConnectionError("Claude CLI exited during startup")

    def is_ready(self) -> bool:
        return self.alive

    async def end_input(self) -> None: ...

    async def close(self) -> None:
        self.alive = False
        if self._turn is not None:
            self._turn.cancel()
        self._outbox.put_nowait(None)

    def die(self) -> None:
        """The process crashes between turns: its output closes and every
        later write fails."""
        self.alive = False
        self._outbox.put_nowait(None)

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self._outbox.get()) is not None:
            yield message

    async def write(self, data: str) -> None:
        if not self.alive:
            raise CLIConnectionError("Claude CLI process is not running")
        message = json.loads(data)
        match message["type"]:
            case "control_request":
                self._answer_sdk(message)
            case "control_response":
                response = message["response"]
                self._awaiting.pop(response["request_id"]).set_result(response)
            case "user":
                self.claude.prompts.append(message["message"]["content"])
                self._turn = asyncio.create_task(self._play(self.claude.next_turn()))

    def _answer_sdk(self, message: dict[str, Any]) -> None:
        request = message["request"]
        if request["subtype"] == "initialize":
            self._hooks = request.get("hooks") or {}
        self._emit(
            type="control_response",
            response={
                "subtype": "success",
                "request_id": message["request_id"],
                "response": {},
            },
        )

    def _emit(self, **message: Any) -> None:
        self._outbox.put_nowait(message)

    def _assistant(self, *content: dict[str, Any]) -> None:
        self._emit(
            type="assistant",
            message={"model": MODEL, "content": list(content)},
            session_id=self.session_id,
        )

    async def _play(self, turn: Turn) -> None:
        denials: list[dict[str, Any]] = []
        ending = EndTurn()
        try:
            self._emit(type="system", subtype="init", session_id=self.session_id)
            for step in turn:
                match step:
                    case ModelDecision():
                        denials += await self._decide(step)
                    case Thinking(text=text):
                        self._assistant(
                            {"type": "thinking", "thinking": text, "signature": ""}
                        )
                    case Raw(message=message):
                        self._outbox.put_nowait(message)
                    case Hold():
                        step.reached.set()
                        await step.released.wait()
                    case EndTurn():
                        ending = step
                    case Hangup():
                        self.alive = False
                        self._outbox.put_nowait(None)
                        return
        # A broken script must fail the test (assert_done), not die unseen in this task.
        except Exception as error:  # noqa: BLE001
            self.claude.errors.append(error)
            ending = EndTurn(is_error=True, result=str(error))
        self._emit(
            type="result",
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=ending.is_error,
            num_turns=1,
            session_id=self.session_id,
            result=ending.result,
            errors=ending.errors,
            api_error_status=ending.api_error_status,
            permission_denials=denials or None,
        )

    async def _decide(self, decision: ModelDecision) -> list[dict[str, Any]]:
        calls = [(f"toolu_{next(self._ids)}", call) for call in decision.tool_calls]
        blocks = [
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": call.name,
                "input": call.arguments,
            }
            for tool_use_id, call in calls
        ]
        if decision.text:
            blocks.insert(0, {"type": "text", "text": decision.text})
        self._assistant(*blocks)
        # Parallel tool uses reach the SDK as concurrent control requests,
        # which it answers independently (one task per request_id).
        outcomes = await asyncio.gather(
            *(self._run_tool(tool_use_id, call) for tool_use_id, call in calls)
        )
        return [denied for denied in outcomes if denied is not None]

    async def _run_tool(
        self, tool_use_id: str, call: ToolCall
    ) -> dict[str, Any] | None:
        """Runs one call the way the CLI does; returns its denial, if denied."""
        if not await self._exists(call.name):
            self._tool_result(
                tool_use_id,
                f"<tool_use_error>Error: No such tool available: {call.name}</tool_use_error>",
                is_error=True,
            )
            return None
        verdict = await self._permission(tool_use_id, call)
        if verdict["behavior"] == "deny":
            self._tool_result(tool_use_id, verdict.get("message", ""), is_error=True)
            return {
                "tool_name": call.name,
                "tool_use_id": tool_use_id,
                "tool_input": call.arguments,
            }
        content, is_error = await self._execute(call)
        self._tool_result(tool_use_id, content, is_error=is_error)
        return None

    async def _permission(self, tool_use_id: str, call: ToolCall) -> dict[str, Any]:
        match await self._pre_tool_use(tool_use_id, call):
            case "deny":
                return {"behavior": "deny", "message": "Blocked by a PreToolUse hook"}
            case "ask":
                return await self._can_use_tool(tool_use_id, call)
        if self._ask_rule_matches(call.name):
            return await self._can_use_tool(tool_use_id, call)
        if self._auto_approved(call.name):
            return {"behavior": "allow"}
        return await self._can_use_tool(tool_use_id, call)

    async def _pre_tool_use(self, tool_use_id: str, call: ToolCall) -> str | None:
        decision = None
        for matcher in self._hooks.get("PreToolUse", []):
            if not re.search(matcher.get("matcher") or ".*", call.name):
                continue
            for callback_id in matcher["hookCallbackIds"]:
                output = await self._ask_sdk(
                    subtype="hook_callback",
                    callback_id=callback_id,
                    input={
                        "hook_event_name": "PreToolUse",
                        "tool_name": call.name,
                        "tool_input": call.arguments,
                    },
                    tool_use_id=tool_use_id,
                )
                specific = output["response"].get("hookSpecificOutput") or {}
                decision = specific.get("permissionDecision", decision)
        return decision

    def _ask_rule_matches(self, tool_name: str) -> bool:
        if "project" not in (self.options.setting_sources or []):
            return False
        return any(
            _allow_rule_matches(rule, tool_name)
            for rule in self.claude.project_ask_rules
        )

    def _auto_approved(self, tool_name: str) -> bool:
        match self.options.permission_mode:
            case "bypassPermissions":
                return True
            case "acceptEdits" if tool_name in EDIT_TOOLS:
                return True
        return any(
            _allow_rule_matches(rule, tool_name) for rule in self.options.allowed_tools
        )

    async def _can_use_tool(self, tool_use_id: str, call: ToolCall) -> dict[str, Any]:
        if (
            self.options.can_use_tool is None
            or self.options.permission_mode == "dontAsk"
        ):
            return {"behavior": "deny", "message": "Permission denied"}
        response = await self._ask_sdk(
            subtype="can_use_tool",
            tool_name=call.name,
            input=call.arguments,
            tool_use_id=tool_use_id,
        )
        return response["response"]

    async def _exists(self, tool_name: str) -> bool:
        if not tool_name.startswith("mcp__"):
            return True
        _, server, tool = tool_name.split("__", 2)
        return tool in await self._served_tools(server)

    async def _served_tools(self, server: str) -> set[str]:
        """The server's tool names, listed once as the CLI does at startup."""
        if server not in self._served:
            await self._mcp(
                server,
                "initialize",
                protocolVersion=MCP_PROTOCOL_VERSION,
                capabilities={},
                clientInfo={"name": "fake-claude", "version": "0"},
            )
            listing = await self._mcp(server, "tools/list")
            self._served[server] = {tool["name"] for tool in listing["result"]["tools"]}
        return self._served[server]

    async def _execute(self, call: ToolCall) -> tuple[Any, bool]:
        if not call.name.startswith("mcp__"):
            return f"{call.name} ran", False
        _, server, tool = call.name.split("__", 2)
        reply = await self._mcp(
            server, "tools/call", name=tool, arguments=call.arguments
        )
        if "error" in reply:
            return reply["error"]["message"], True
        result = reply["result"]
        return result["content"], bool(result.get("isError"))

    async def _mcp(self, server: str, method: str, **params: Any) -> dict[str, Any]:
        response = await self._ask_sdk(
            subtype="mcp_message",
            server_name=server,
            message={
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": method,
                "params": params,
            },
        )
        return response["response"]["mcp_response"]

    def _tool_result(self, tool_use_id: str, content: Any, *, is_error: bool) -> None:
        self._emit(
            type="user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": is_error,
                    }
                ]
            },
            session_id=self.session_id,
        )

    async def _ask_sdk(self, **request: Any) -> dict[str, Any]:
        request_id = f"cli_{next(self._ids)}"
        answer = asyncio.get_running_loop().create_future()
        self._awaiting[request_id] = answer
        self._emit(type="control_request", request_id=request_id, request=request)
        response = await answer
        if response["subtype"] == "error":
            raise RuntimeError(response["error"])
        return response


def _allow_rule_matches(rule: str, tool_name: str) -> bool:
    """Bare names match exactly; ``mcp__<server>__*`` globs a server's tools."""
    if rule.startswith("mcp__") and rule.endswith("*"):
        return tool_name.startswith(rule[:-1])
    return rule == tool_name
