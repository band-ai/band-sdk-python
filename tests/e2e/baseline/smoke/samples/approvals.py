"""How each coding agent asks a room to approve a gated command, and how a human answers.

The matrix builders run every coding agent headless (auto-accept, or Codex's
``approvalPolicy="never"``) and their ``prompt``/``features``/``tools`` contract can't
express a manual approval mode, so the approval smoke builds each adapter here instead.
Every room-visible line -- the request a token is read from, the reply, the notice that
proves the reply was recognized -- comes from the adapter's own template or command
enum, so a reworded prompt fails the smoke instead of silently drifting from it.
"""

from __future__ import annotations

import asyncio
import codecs
import re
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from band_rest import ChatMessage

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE as CLAUDE_REQUESTED,
)
from band.adapters.claude_sdk import (
    APPROVAL_RESOLVED_TEMPLATE as CLAUDE_RESOLVED,
)
from band.adapters.claude_sdk import (
    APPROVAL_TIMED_OUT_TEMPLATE as CLAUDE_TIMED_OUT,
)
from band.adapters.claude_sdk import (
    APPROVAL_UNAUTHORIZED_MESSAGE,
    APPROVAL_UNKNOWN_TOKEN_TEMPLATE,
    ClaudeSDKAdapter,
    ClaudeSDKCommand,
)
from band.adapters.codex import (
    APPROVAL_REQUESTED_TEMPLATE as CODEX_REQUESTED,
)
from band.adapters.codex import (
    APPROVAL_RESOLVED_TEMPLATE as CODEX_RESOLVED,
)
from band.adapters.codex import (
    APPROVAL_TIMED_OUT_TEMPLATE as CODEX_TIMED_OUT,
)
from band.adapters.codex import (
    NO_APPROVALS_TO_RESOLVE_MESSAGE,
    CodexAdapter,
    CodexAdapterConfig,
    CodexCommand,
    CodexSandboxMode,
)
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.adapters.opencode.approvals import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_NO_LONGER_PENDING_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    PermissionReplyWord,
    format_question_prompt,
)
from band.adapters.opencode.approvals import (
    APPROVAL_REQUESTED_TEMPLATE as OPENCODE_REQUESTED,
)
from band.client.streaming import MessageCreatedPayload
from band.core.simple_adapter import SimpleAdapter
from band.core.types import MessageType
from band.integrations.acp.cursor import (
    DECISION_NOT_PENDING_TEMPLATE,
    DECISION_RESOLVED_TEMPLATE,
    DECISION_TIMED_OUT_TEMPLATE,
    DECISION_UNAUTHORIZED_MESSAGE,
    PERMISSION_REQUESTED_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from band.integrations.codex.types import CodexApprovalMethod
from band.runtime.formatters import strip_leading_mentions
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.e2e.baseline.toolkit.builders import codex_config_kwargs
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.deps import Dep
from tests.e2e.baseline.toolkit.observations.matching import tolerant_match

SHELL_PROMPT = "Keep responses short. Use your shell tool when asked."
# Poll cadence for Notice.assert_shown's event read (see its docstring): there's
# no push channel for non-text events to key a barrier off, so this bounded
# poll is the least-bad option.
_EVENT_POLL_INTERVAL_S = 0.5
# Formatting a model may wrap its closing word in.
_MARKER_DECORATION = "`*_.!'\""


class Outcome(StrEnum):
    """How the human answers the approval request."""

    APPROVE = "approve"
    DECLINE = "decline"
    TIMEOUT = "timeout"  # never answers, so the adapter's wait expires


def marker_command(marker: str, target: Path) -> str:
    """Write ``marker`` to ``target`` in the agent's configured workdir."""
    return f'echo {marker} > "{target.name}"'


def command_request(marker: str, target: Path, *, done: str) -> str:
    """Ask for exactly one shell command whose only effect is writing ``marker``."""
    return (
        f"Use your shell tool to run exactly `{marker_command(marker, target)}`. "
        "You must execute it with the tool, not answer from memory. "
        "If permission is declined or expires, do not retry with another tool or shell. "
        "Do not run a second shell command to check the result. "
        f"After the tool attempt is resolved, finish with exactly `{done}`."
    )


def written_lines(target: Path) -> list[str]:
    """The lines a shell redirect wrote to ``target``, whichever host shell wrote them.

    Windows PowerShell 5.1 redirects as UTF-16 with a BOM; cmd, bash and pwsh
    write UTF-8, and cmd keeps the space before its redirect.
    """
    data = target.read_bytes()
    encoding = "utf-16" if data.startswith(codecs.BOM_UTF16_LE) else "utf-8-sig"
    return [line.rstrip() for line in data.decode(encoding).splitlines()]


def closes_with(content: str, marker: str) -> bool:
    """Whether a message ends with ``marker`` as its final word."""
    words = strip_leading_mentions(content).split()
    return bool(words) and words[-1].strip(_MARKER_DECORATION) == marker


def appending_command(marker: str, target: Path) -> str:
    """Append ``marker`` in the configured workdir, so each run shows."""
    return f'echo {marker} >> "{target.name}"'


def repeat_request(command: str, done: str) -> str:
    """Ask for ``command`` twice, as two tool calls, then a closing word."""
    return (
        f"Use your shell tool to run exactly `{command}`, then run exactly the same "
        "command a second time as its own separate tool call. You must execute both "
        f"with the tool. When both have run, reply with exactly `{done}`."
    )


def commands_request(*commands: str, done: str) -> str:
    """Ask for each command as its own tool call, none retried after a decline."""
    listed = " and ".join(f"`{command}`" for command in commands)
    return (
        f"Use your shell tool to run exactly these commands: {listed}. Run each one "
        "as its own separate tool call, never combined into one command. If a "
        "command is declined, do not retry it. Do not run any further shell "
        f"commands. When both attempts are resolved, finish with exactly `{done}`."
    )


def question_request() -> str:
    """Ask the agent to put a question to the room and echo the answer."""
    return (
        "Use your question tool to ask me which codeword to use. After I answer, "
        "reply with exactly the codeword I gave you and nothing else."
    )


def template_pattern(template: str, *, token: str = "token") -> re.Pattern[str]:
    """A regex matching what ``template`` renders, capturing each field by name.

    ``token`` names the field captured as the ``token`` group. A field repeated in
    the template must repeat its first value; the last field runs to the line end.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for literal, name, _spec, _conversion in string.Formatter().parse(template):
        parts.append(re.escape(literal))
        if name is None:
            continue
        group = "token" if name == token else name
        parts.append(f"(?P={group})" if group in seen else f"(?P<{group}>.+?)")
        seen.add(group)
    return re.compile("".join(parts) + "$", re.DOTALL | re.MULTILINE)


@dataclass(frozen=True)
class Notice:
    """The adapter's confirmation of an outcome: a chat message the capture streams,
    or an ``error`` event, which only a post-turn read sees."""

    text: str
    message_type: MessageType = MessageType.TEXT

    def streamed_in(self, contents: list[str]) -> bool:
        """Whether ``contents`` show the notice, vacuously so for an event notice."""
        return self.message_type != MessageType.TEXT or any(
            self.text in content for content in contents
        )

    async def assert_shown(
        self, capture: ReplyCapture, agent_id: str, *, deadline_s: float = 10.0
    ) -> None:
        """The notice reached the room: a captured chat message, or (for an event
        notice) a REST-visible event.

        The event read races the adapter's own send -- e.g. OpenCode's TIMEOUT
        notice is emitted only after the closing chat reply already unblocked the
        room (``RoomApprovals._expire_permission`` awaits the permission reply
        first, the notice event second) -- so a single REST read here can run
        before the event row exists. Poll instead of trusting one snapshot.
        """
        if self.message_type == MessageType.TEXT:
            capture.messages.assert_contains_any([self.text])
            return

        async def until_shown() -> Any:
            while True:
                shown = await capture.events(self.message_type, sender_id=agent_id)
                if any(tolerant_match(self.text, item.content) for item in shown):
                    return shown
                await asyncio.sleep(_EVENT_POLL_INTERVAL_S)

        try:
            shown = await asyncio.wait_for(until_shown(), timeout=deadline_s)
        except TimeoutError:
            shown = await capture.events(self.message_type, sender_id=agent_id)
        shown.assert_contains_any([self.text])


@dataclass(frozen=True)
class AgentSetup:
    """How a manual-approval agent is rooted and who may decide its asks."""

    workdir: Path
    wait_timeout_s: float
    # Sender ids allowed to decide; None admits anyone.
    approvers: frozenset[str] | None = None


@dataclass(frozen=True)
class SessionApproval:
    """Approving an ask so that a repeat of the same command no longer asks."""

    reply: Callable[[re.Match[str]], str]
    notice: Callable[[re.Match[str]], Notice]


@dataclass(frozen=True)
class QuestionRelay:
    """How the agent puts a question to the room, and confirms the answer."""

    request: re.Pattern[str]
    answered: Callable[[re.Match[str]], Notice]

    def find(self, messages: list[MessageCreatedPayload]) -> re.Match[str] | None:
        matches = (self.request.search(m.content or "") for m in messages)
        return next((m for m in matches if m), None)


@dataclass(frozen=True)
class ApprovalDialect:
    """One coding agent's manual-approval build and room vocabulary.

    ``build(settings, setup)`` roots the agent in ``setup.workdir``;
    ``reply``/``notice`` take the outcome and the matched approval request, and
    ``late_notice`` is what a reply to an ask that already expired gets, and
    ``shell_command`` reads the command a request gates, where it renders one. The
    optional parts name what only some agents can do: ``refusal`` (restricting
    who decides), ``session_approval`` and ``question``.
    """

    build: Callable[[BaselineSettings, AgentSetup], SimpleAdapter[Any]]
    request: re.Pattern[str]
    reply: Callable[[Outcome, re.Match[str]], str]
    notice: Callable[[Outcome, re.Match[str]], Notice]
    late_notice: Callable[[re.Match[str]], Notice]
    shell_command: Callable[[re.Match[str]], str | None]
    refusal: Notice | None = None
    session_approval: SessionApproval | None = None
    question: QuestionRelay | None = None
    # Beyond the cell's own requirements: what makes the backend actually ask.
    extra_deps: tuple[Dep, ...] = ()
    # Where the workdir must live (Codex may only touch a disposable root).
    workdir_root: Callable[[BaselineSettings], str | None] = field(
        default=lambda _settings: None
    )

    def find_request(
        self, messages: list[MessageCreatedPayload | ChatMessage]
    ) -> re.Match[str] | None:
        """The first approval request among ``messages``, if the agent posted one."""
        return next(iter(self.find_requests(messages)), None)

    def find_requests(
        self, messages: list[MessageCreatedPayload | ChatMessage]
    ) -> list[re.Match[str]]:
        """Every distinct approval request among ``messages``, in order."""
        found: dict[str, re.Match[str]] = {}
        for message in messages:
            for match in self.request.finditer(message.content or ""):
                found.setdefault(match["token"], match)
        return list(found.values())

    def settled(
        self,
        since_request: list[MessageCreatedPayload | ChatMessage],
        *notices: Notice,
        closing_reply: str,
    ) -> bool:
        """Whether every notice is shown and a message closing with the requested
        final word followed it."""
        contents = [m.content or "" for m in since_request]
        if not all(notice.streamed_in(contents) for notice in notices):
            return False
        last_control = max(
            (
                index
                for index, content in enumerate(contents)
                if self.request.search(content)
                or any(notice.text in content for notice in notices)
            ),
            default=-1,
        )
        return any(
            closes_with(content, closing_reply)
            and self.request.search(content) is None
            and not any(notice.text in content for notice in notices)
            for content in contents[last_control + 1 :]
        )


def _claude_sdk(settings: BaselineSettings, setup: AgentSetup) -> SimpleAdapter[Any]:
    return ClaudeSDKAdapter(
        model=settings.llm_models.anthropic_model,
        custom_section=SHELL_PROMPT,
        cwd=str(setup.workdir),
        approval_mode="manual",
        approval_wait_timeout_s=setup.wait_timeout_s,
        approval_authorized_senders=setup.approvers,
    )


def _summary_command(summary: str) -> Callable[[re.Match[str]], str | None]:
    """Read the gated command out of a request's ``summary`` field, given the
    adapter's own summary rendered with ``{command}`` placeholders."""
    pattern = template_pattern(summary)

    def command(request: re.Match[str]) -> str | None:
        match = pattern.fullmatch(request["summary"])
        return match["command"] if match else None

    return command


def _opencode_shell_command(request: re.Match[str]) -> str | None:
    return request["patterns"] if request["permission"] == "bash" else None


def _claude_sdk_reply(outcome: Outcome, request: re.Match[str]) -> str:
    command = (
        ClaudeSDKCommand.APPROVE
        if outcome is Outcome.APPROVE
        else ClaudeSDKCommand.DECLINE
    )
    return f"/{command} {request['token']}"


def _claude_sdk_notice(outcome: Outcome, request: re.Match[str]) -> Notice:
    token = request["token"]
    match outcome:
        case Outcome.APPROVE:
            return Notice(CLAUDE_RESOLVED.format(token=token, decision="accept"))
        case Outcome.DECLINE:
            return Notice(CLAUDE_RESOLVED.format(token=token, decision="decline"))
        case Outcome.TIMEOUT:
            return Notice(CLAUDE_TIMED_OUT.format(token=token, decision="decline"))


def _codex(settings: BaselineSettings, setup: AgentSetup) -> SimpleAdapter[Any]:
    # A read-only sandbox under "on-request" makes Codex escalate any write to the
    # room for approval; "never" (the matrix default) would never ask.
    config = codex_config_kwargs(settings, prompt=SHELL_PROMPT) | {
        "workspace_for_room": lambda _room_id: str(setup.workdir),
        "approval_mode": "manual",
        "approval_policy": "on-request",
        "sandbox": CodexSandboxMode.READ_ONLY,
        "approval_wait_timeout_s": setup.wait_timeout_s,
    }
    return CodexAdapter(config=CodexAdapterConfig(**config))


def _codex_reply(outcome: Outcome, request: re.Match[str]) -> str:
    command = (
        CodexCommand.APPROVE if outcome is Outcome.APPROVE else CodexCommand.DECLINE
    )
    return f"/{command} {request['token']}"


def _codex_notice(outcome: Outcome, request: re.Match[str]) -> Notice:
    token = request["token"]
    match outcome:
        case Outcome.APPROVE:
            return Notice(CODEX_RESOLVED.format(token=token, decision="accept"))
        case Outcome.DECLINE:
            return Notice(CODEX_RESOLVED.format(token=token, decision="decline"))
        case Outcome.TIMEOUT:
            return Notice(CODEX_TIMED_OUT.format(token=token, decision="decline"))


def _cursor(settings: BaselineSettings, setup: AgentSetup) -> SimpleAdapter[Any]:
    from band.adapters.cursor_acp import (  # noqa: PLC0415
        CursorACPAdapter,
        CursorACPAdapterConfig,
    )

    config_kwargs: dict[str, Any] = {
        "api_key": settings.backends.cursor_api_key,
        "custom_section": SHELL_PROMPT,
        "cwd": str(setup.workdir),
        "approval_mode": "manual",
        # Only the permission is under test; other decisions resolve themselves.
        "question_mode": "auto_first",
        "plan_mode": "auto_accept",
        "decision_timeout_s": setup.wait_timeout_s,
        "decision_authorized_senders": setup.approvers,
    }
    if settings.backends.cursor_command.strip():
        config_kwargs["command"] = tuple(settings.backends.cursor_command.split())
    return CursorACPAdapter(config=CursorACPAdapterConfig(**config_kwargs))


def _allow_option(options: str, *, lasting: bool) -> str:
    """The allow option among a permission request's option ids: the one-shot
    one, or with ``lasting`` the one that also covers repeats."""
    allowed = [option for option in options.split(", ") if option.startswith("allow")]
    assert allowed, f"Cursor offered no allow option: {options}"
    return min(allowed, key=lambda option: ("once" in option) == lasting)


def _cursor_select(request: re.Match[str], *, lasting: bool) -> str:
    choice = _allow_option(request["options"], lasting=lasting)
    return f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {request['token']} {choice}"


def _cursor_reply(outcome: Outcome, request: re.Match[str]) -> str:
    if outcome is Outcome.APPROVE:
        return _cursor_select(request, lasting=False)
    return f"{ROOM_COMMAND} {CursorCommandWord.DENY} {request['token']}"


def _cursor_notice(outcome: Outcome, request: re.Match[str]) -> Notice:
    template = (
        DECISION_TIMED_OUT_TEMPLATE
        if outcome is Outcome.TIMEOUT
        else DECISION_RESOLVED_TEMPLATE
    )
    return Notice(template.format(kind="permission", token=request["token"]))


def _opencode(settings: BaselineSettings, setup: AgentSetup) -> SimpleAdapter[Any]:
    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.backends.opencode_base_url,
            provider_id=settings.backends.opencode_provider_id,
            model_id=settings.backends.opencode_model_id,
            custom_section=SHELL_PROMPT,
            directory=str(setup.workdir),
            approval_mode="manual",
            approval_wait_timeout_s=setup.wait_timeout_s,
            question_mode="manual",
            question_wait_timeout_s=setup.wait_timeout_s,
        )
    )


def _opencode_reply(outcome: Outcome, request: re.Match[str]) -> str:
    word = (
        PermissionReplyWord.APPROVE
        if outcome is Outcome.APPROVE
        else PermissionReplyWord.REJECT
    )
    return f"{word} {request['token']}"


def _opencode_notice(outcome: Outcome, request: re.Match[str]) -> Notice:
    request_id = request["token"]
    match outcome:
        case Outcome.APPROVE:
            return Notice(
                APPROVAL_HANDLED_TEMPLATE.format(request_id=request_id, reply="once")
            )
        case Outcome.DECLINE:
            return Notice(
                APPROVAL_HANDLED_TEMPLATE.format(request_id=request_id, reply="reject")
            )
        case Outcome.TIMEOUT:
            return Notice(
                APPROVAL_TIMED_OUT_TEMPLATE.format(
                    request_id=request_id, reply="reject"
                ),
                MessageType.ERROR,
            )


def _cursor_resolved(request: re.Match[str]) -> Notice:
    return Notice(
        DECISION_RESOLVED_TEMPLATE.format(kind="permission", token=request["token"])
    )


def _codex_session_notice(request: re.Match[str]) -> Notice:
    return Notice(
        CODEX_RESOLVED.format(token=request["token"], decision="acceptForSession")
    )


def _opencode_handled(reply: str) -> Callable[[re.Match[str]], Notice]:
    return lambda request: Notice(
        APPROVAL_HANDLED_TEMPLATE.format(request_id=request["token"], reply=reply)
    )


OPENCODE_QUESTION = QuestionRelay(
    request=template_pattern(
        format_question_prompt([], "{request_id}").splitlines()[0], token="request_id"
    ),
    answered=lambda request: Notice(
        f"OpenCode question `{request['token']}` answered."
    ),
)

DIALECTS: dict[Adapter, ApprovalDialect] = {
    Adapter.CLAUDE_SDK: ApprovalDialect(
        build=_claude_sdk,
        request=template_pattern(CLAUDE_REQUESTED),
        reply=_claude_sdk_reply,
        notice=_claude_sdk_notice,
        late_notice=lambda request: Notice(
            APPROVAL_UNKNOWN_TOKEN_TEMPLATE.format(
                token=request["token"], available="none"
            )
        ),
        shell_command=_summary_command(
            ClaudeSDKAdapter._approval_summary("{tool}", {"command": "{command}"})
        ),
        refusal=Notice(APPROVAL_UNAUTHORIZED_MESSAGE),
    ),
    Adapter.CODEX: ApprovalDialect(
        build=_codex,
        request=template_pattern(CODEX_REQUESTED),
        reply=_codex_reply,
        notice=_codex_notice,
        late_notice=lambda _request: Notice(NO_APPROVALS_TO_RESOLVE_MESSAGE),
        shell_command=_summary_command(
            CodexAdapter._approval_summary(
                CodexApprovalMethod.COMMAND_EXECUTION, {"command": "{command}"}
            )
        ),
        session_approval=SessionApproval(
            reply=lambda request: f"/{CodexCommand.APPROVE_SESSION} {request['token']}",
            notice=_codex_session_notice,
        ),
        workdir_root=lambda settings: settings.backends.codex_cwd,
    ),
    Adapter.CURSOR_ACP: ApprovalDialect(
        build=_cursor,
        request=template_pattern(PERMISSION_REQUESTED_TEMPLATE),
        reply=_cursor_reply,
        notice=_cursor_notice,
        late_notice=lambda request: Notice(
            DECISION_NOT_PENDING_TEMPLATE.format(token=request["token"])
        ),
        shell_command=lambda request: request["tool"],
        refusal=Notice(DECISION_UNAUTHORIZED_MESSAGE),
        session_approval=SessionApproval(
            reply=lambda request: _cursor_select(request, lasting=True),
            notice=_cursor_resolved,
        ),
    ),
    Adapter.OPENCODE: ApprovalDialect(
        build=_opencode,
        request=template_pattern(OPENCODE_REQUESTED, token="request_id"),
        reply=_opencode_reply,
        notice=_opencode_notice,
        late_notice=lambda request: Notice(
            APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(request_id=request["token"])
        ),
        shell_command=_opencode_shell_command,
        session_approval=SessionApproval(
            reply=lambda request: f"{PermissionReplyWord.ALWAYS} {request['token']}",
            notice=_opencode_handled("always"),
        ),
        question=OPENCODE_QUESTION,
        # The serve's permission rules, not approval_mode, decide whether bash asks.
        extra_deps=(Dep.OPENCODE_BASH_ASKS,),
    ),
}
