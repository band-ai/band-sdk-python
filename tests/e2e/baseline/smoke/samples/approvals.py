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
import re
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

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
from band.adapters.cursor_acp import (
    DECISION_NOT_PENDING_TEMPLATE,
    DECISION_RESOLVED_TEMPLATE,
    DECISION_TIMED_OUT_TEMPLATE,
    DECISION_UNAUTHORIZED_MESSAGE,
    PERMISSION_REQUESTED_TEMPLATE,
    ROOM_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
    CursorCommandWord,
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


class Outcome(StrEnum):
    """How the human answers the approval request."""

    APPROVE = "approve"
    DECLINE = "decline"
    TIMEOUT = "timeout"  # never answers, so the adapter's wait expires


def marker_command(marker: str, target: Path) -> str:
    """The one shell command whose only effect is writing ``marker`` to ``target``."""
    return f"printf %s {marker} > {target}"


def command_request(marker: str, target: Path) -> str:
    """Ask for exactly one shell command whose only effect is writing ``marker``."""
    return (
        f"Use your shell tool to run exactly `{marker_command(marker, target)}`. "
        "You must execute it with the tool, not answer from memory."
    )


def appending_command(marker: str, target: Path) -> str:
    """A shell command that appends ``marker`` to ``target``, so each run shows."""
    return f"printf %s {marker} >> {target}"


def repeat_request(command: str, done: str) -> str:
    """Ask for ``command`` twice, as two tool calls, then a closing word."""
    return (
        f"Use your shell tool to run exactly `{command}`, then run exactly the same "
        "command a second time as its own separate tool call. You must execute both "
        f"with the tool. When both have run, reply with exactly `{done}`."
    )


def commands_request(*commands: str) -> str:
    """Ask for each command as its own tool call, none retried after a decline."""
    listed = " and ".join(f"`{command}`" for command in commands)
    return (
        f"Use your shell tool to run exactly these commands: {listed}. Run each one "
        "as its own separate tool call, never combined into one command. If a "
        "command is declined, do not retry it; just say so and continue."
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
    ``late_notice`` is what a reply to an ask that already expired gets. The
    optional parts name what only some agents can do: ``refusal`` (restricting
    who decides), ``session_approval`` and ``question``.
    """

    build: Callable[[BaselineSettings, AgentSetup], SimpleAdapter[Any]]
    request: re.Pattern[str]
    reply: Callable[[Outcome, re.Match[str]], str]
    notice: Callable[[Outcome, re.Match[str]], Notice]
    late_notice: Callable[[re.Match[str]], Notice]
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
        self, messages: list[MessageCreatedPayload]
    ) -> re.Match[str] | None:
        """The first approval request among ``messages``, if the agent posted one."""
        return next(iter(self.find_requests(messages)), None)

    def find_requests(
        self, messages: list[MessageCreatedPayload]
    ) -> list[re.Match[str]]:
        """Every distinct approval request among ``messages``, in order."""
        found: dict[str, re.Match[str]] = {}
        for message in messages:
            for match in self.request.finditer(message.content or ""):
                found.setdefault(match["token"], match)
        return list(found.values())

    def settled(
        self, since_request: list[MessageCreatedPayload], *notices: Notice
    ) -> bool:
        """Whether the turn has played out past its decisions: every notice is shown
        and the agent has closed the turn with a reply that is none of them."""
        contents = [m.content or "" for m in since_request]
        closed = any(
            self.request.search(content) is None
            and not any(notice.text in content for notice in notices)
            for content in contents
        )
        return closed and all(notice.streamed_in(contents) for notice in notices)


def _claude_sdk(settings: BaselineSettings, setup: AgentSetup) -> SimpleAdapter[Any]:
    return ClaudeSDKAdapter(
        model=settings.llm_models.anthropic_model,
        custom_section=SHELL_PROMPT,
        cwd=str(setup.workdir),
        approval_mode="manual",
        approval_wait_timeout_s=setup.wait_timeout_s,
        approval_authorized_senders=setup.approvers,
    )


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
        refusal=Notice(APPROVAL_UNAUTHORIZED_MESSAGE),
    ),
    Adapter.CODEX: ApprovalDialect(
        build=_codex,
        request=template_pattern(CODEX_REQUESTED),
        reply=_codex_reply,
        notice=_codex_notice,
        late_notice=lambda _request: Notice(NO_APPROVALS_TO_RESOLVE_MESSAGE),
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
        session_approval=SessionApproval(
            reply=lambda request: f"{PermissionReplyWord.ALWAYS} {request['token']}",
            notice=_opencode_handled("always"),
        ),
        question=OPENCODE_QUESTION,
        # The serve's permission rules, not approval_mode, decide whether bash asks.
        extra_deps=(Dep.OPENCODE_BASH_ASKS,),
    ),
}
