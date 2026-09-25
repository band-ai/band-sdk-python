"""How each coding agent asks a room to approve a gated command, and how a human answers.

The matrix builders run every coding agent headless (auto-accept, or Codex's
``approvalPolicy="never"``) and their ``prompt``/``features``/``tools`` contract can't
express a manual approval mode, so the approval smoke builds each adapter here instead.
Every room-visible line -- the request a token is read from, the reply, the notice that
proves the reply was recognized -- comes from the adapter's own template or command
enum, so a reworded prompt fails the smoke instead of silently drifting from it.
"""

from __future__ import annotations

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
from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKCommand
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
    CodexAdapter,
    CodexAdapterConfig,
    CodexCommand,
    CodexSandboxMode,
)
from band.adapters.cursor_acp import (
    DECISION_RESOLVED_TEMPLATE,
    DECISION_TIMED_OUT_TEMPLATE,
    PERMISSION_REQUESTED_TEMPLATE,
    ROOM_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
    CursorCommandWord,
)
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.adapters.opencode.approvals import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    PermissionReplyWord,
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

SHELL_PROMPT = "Keep responses short. Use your shell tool when asked."


class Outcome(StrEnum):
    """How the human answers the approval request."""

    APPROVE = "approve"
    DECLINE = "decline"
    TIMEOUT = "timeout"  # never answers, so the adapter's wait expires


def command_request(marker: str, target: Path) -> str:
    """Ask for exactly one shell command whose only effect is writing ``marker``."""
    return (
        f"Use your shell tool to run exactly `printf %s {marker} > {target}`. "
        "You must execute it with the tool, not answer from memory."
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

    async def assert_shown(self, capture: ReplyCapture, agent_id: str) -> None:
        shown = (
            capture.messages
            if self.message_type == MessageType.TEXT
            else await capture.events(self.message_type, sender_id=agent_id)
        )
        shown.assert_contains_any([self.text])


@dataclass(frozen=True)
class ApprovalDialect:
    """One coding agent's manual-approval build and room vocabulary.

    ``build(settings, workdir, wait_timeout_s)`` roots the agent in ``workdir``;
    ``reply``/``notice`` take the outcome and the matched approval request.
    """

    build: Callable[[BaselineSettings, Path, float], SimpleAdapter[Any]]
    request: re.Pattern[str]
    reply: Callable[[Outcome, re.Match[str]], str]
    notice: Callable[[Outcome, re.Match[str]], Notice]
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
        matches = (self.request.search(m.content or "") for m in messages)
        return next((m for m in matches if m), None)

    def settled(
        self, notice: Notice, since_request: list[MessageCreatedPayload]
    ) -> bool:
        """Whether the turn has played out past the decision: the notice is shown and
        the agent has closed the turn with a reply that is neither."""
        contents = [m.content or "" for m in since_request]
        closed = any(
            self.request.search(content) is None and notice.text not in content
            for content in contents
        )
        return closed and notice.streamed_in(contents)


def _claude_sdk(
    settings: BaselineSettings, workdir: Path, wait_timeout_s: float
) -> SimpleAdapter[Any]:
    return ClaudeSDKAdapter(
        model=settings.llm_models.anthropic_model,
        custom_section=SHELL_PROMPT,
        cwd=str(workdir),
        approval_mode="manual",
        approval_wait_timeout_s=wait_timeout_s,
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


def _codex(
    settings: BaselineSettings, workdir: Path, wait_timeout_s: float
) -> SimpleAdapter[Any]:
    # A read-only sandbox under "on-request" makes Codex escalate any write to the
    # room for approval; "never" (the matrix default) would never ask.
    config = codex_config_kwargs(settings, prompt=SHELL_PROMPT) | {
        "workspace_for_room": lambda _room_id: str(workdir),
        "approval_mode": "manual",
        "approval_policy": "on-request",
        "sandbox": CodexSandboxMode.READ_ONLY,
        "approval_wait_timeout_s": wait_timeout_s,
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


def _cursor(
    settings: BaselineSettings, workdir: Path, wait_timeout_s: float
) -> SimpleAdapter[Any]:
    config_kwargs: dict[str, Any] = {
        "api_key": settings.backends.cursor_api_key,
        "custom_section": SHELL_PROMPT,
        "cwd": str(workdir),
        "approval_mode": "manual",
        # Only the permission is under test; other decisions resolve themselves.
        "question_mode": "auto_first",
        "plan_mode": "auto_accept",
        "decision_timeout_s": wait_timeout_s,
    }
    if settings.backends.cursor_command.strip():
        config_kwargs["command"] = tuple(settings.backends.cursor_command.split())
    return CursorACPAdapter(config=CursorACPAdapterConfig(**config_kwargs))


def _allow_once(options: str) -> str:
    """The least-privilege allow option among a permission request's option ids."""
    allowed = [option for option in options.split(", ") if option.startswith("allow")]
    assert allowed, f"Cursor offered no allow option: {options}"
    return min(allowed, key=lambda option: "once" not in option)


def _cursor_reply(outcome: Outcome, request: re.Match[str]) -> str:
    token = request["token"]
    if outcome is Outcome.APPROVE:
        choice = _allow_once(request["options"])
        return f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {token} {choice}"
    return f"{ROOM_COMMAND} {CursorCommandWord.DENY} {token}"


def _cursor_notice(outcome: Outcome, request: re.Match[str]) -> Notice:
    template = (
        DECISION_TIMED_OUT_TEMPLATE
        if outcome is Outcome.TIMEOUT
        else DECISION_RESOLVED_TEMPLATE
    )
    return Notice(template.format(kind="permission", token=request["token"]))


def _opencode(
    settings: BaselineSettings, workdir: Path, wait_timeout_s: float
) -> SimpleAdapter[Any]:
    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.backends.opencode_base_url,
            provider_id=settings.backends.opencode_provider_id,
            model_id=settings.backends.opencode_model_id,
            custom_section=SHELL_PROMPT,
            directory=str(workdir),
            approval_mode="manual",
            approval_wait_timeout_s=wait_timeout_s,
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


DIALECTS: dict[Adapter, ApprovalDialect] = {
    Adapter.CLAUDE_SDK: ApprovalDialect(
        build=_claude_sdk,
        request=template_pattern(CLAUDE_REQUESTED),
        reply=_claude_sdk_reply,
        notice=_claude_sdk_notice,
    ),
    Adapter.CODEX: ApprovalDialect(
        build=_codex,
        request=template_pattern(CODEX_REQUESTED),
        reply=_codex_reply,
        notice=_codex_notice,
        workdir_root=lambda settings: settings.backends.codex_cwd,
    ),
    Adapter.CURSOR_ACP: ApprovalDialect(
        build=_cursor,
        request=template_pattern(PERMISSION_REQUESTED_TEMPLATE),
        reply=_cursor_reply,
        notice=_cursor_notice,
    ),
    Adapter.OPENCODE: ApprovalDialect(
        build=_opencode,
        request=template_pattern(OPENCODE_REQUESTED, token="request_id"),
        reply=_opencode_reply,
        notice=_opencode_notice,
        # The serve's permission rules, not approval_mode, decide whether bash asks.
        extra_deps=(Dep.OPENCODE_BASH_ASKS,),
    ),
}
