"""The `/<word>` room commands the Codex adapter handles itself."""

from __future__ import annotations

from enum import StrEnum

from band.adapters.codex.approvals import ApprovalDecision


class CodexCommand(StrEnum):
    """The `/<word>` room commands this adapter handles itself, rather than
    forwarding to Codex -- the single source for parsing, dispatch, and the
    command names quoted back in room messages."""

    HELP = "help"
    STATUS = "status"
    MODEL = "model"
    MODELS = "models"
    REASONING = "reasoning"
    APPROVALS = "approvals"
    APPROVE = "approve"
    APPROVE_SESSION = "approve-session"
    DECLINE = "decline"
    SANDBOX = "sandbox"
    PERMISSIONS = "permissions"
    THREADS = "threads"
    THREAD = "thread"
    USAGE = "usage"


# The decision each resolving approval command sends back to Codex.
APPROVAL_COMMAND_DECISIONS: dict[CodexCommand, ApprovalDecision] = {
    CodexCommand.APPROVE: "accept",
    CodexCommand.APPROVE_SESSION: "acceptForSession",
    CodexCommand.DECLINE: "decline",
}


APPROVAL_COMMANDS = frozenset({*APPROVAL_COMMAND_DECISIONS, CodexCommand.APPROVALS})


# Local commands that ask the room's Codex process.
PROCESS_COMMANDS = frozenset(
    {CodexCommand.MODEL, CodexCommand.MODELS, CodexCommand.REASONING}
)


# Membership by plain string: Python 3.11's Enum rejects `"word" in CodexCommand`.
COMMAND_WORDS = frozenset(CodexCommand)


APPROVAL_REQUESTED_TEMPLATE = (
    "Approval requested ({summary}). Approval id: `{token}`. "
    f"Reply `/{CodexCommand.APPROVE} {{token}}` or "
    f"`/{CodexCommand.DECLINE} {{token}}` or "
    f"`/{CodexCommand.APPROVE_SESSION} {{token}}` "
    "(approve all similar for this session). "
    f"Use `/{CodexCommand.APPROVALS}` to list pending approvals."
)


APPROVAL_RESOLVED_TEMPLATE = "Approval `{token}` resolved as `{decision}`."


APPROVAL_TIMED_OUT_TEMPLATE = "Approval `{token}` timed out. Applied `{decision}`."


NO_APPROVALS_TO_RESOLVE_MESSAGE = "No pending approvals to resolve."


class CodexSubcommand(StrEnum):
    """Second words of `/model` and `/thread`."""

    LIST = "list"
    LS = "ls"
    INFO = "info"
    ARCHIVE = "archive"


MODEL_LIST_WORDS = frozenset({CodexSubcommand.LIST, CodexSubcommand.LS})


HELP_TEXT = (
    "Codex commands: "
    f"`/{CodexCommand.STATUS}`, `/{CodexCommand.MODEL}`, `/{CodexCommand.MODELS}`, "
    f"`/{CodexCommand.MODEL} {CodexSubcommand.LIST}`, "
    f"`/{CodexCommand.MODELS} {CodexSubcommand.LIST}`, `/{CodexCommand.MODEL} <id>`, "
    f"`/{CodexCommand.REASONING} [effort]`, "
    f"`/{CodexCommand.APPROVALS}`, `/{CodexCommand.APPROVE} <id>`, "
    f"`/{CodexCommand.APPROVE_SESSION} <id>`, `/{CodexCommand.DECLINE} <id>`, "
    f"`/{CodexCommand.THREADS}`, `/{CodexCommand.THREAD} {CodexSubcommand.INFO}`, "
    f"`/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}`, "
    f"`/{CodexCommand.SANDBOX} <mode>`, `/{CodexCommand.PERMISSIONS}`, "
    f"`/{CodexCommand.USAGE}`, `/{CodexCommand.HELP}`."
)
