"""Observable Cursor decision commands and native permission selections."""

from __future__ import annotations

from acp.schema import AllowedOutcome, PermissionOption

from band.integrations.acp.cursor import CursorCommandWord
from band.integrations.acp.permissions import ALLOW_ALWAYS_KIND, ALLOW_ONCE_KIND
from tests.integrations.acp.acp_toolkit import FakeACPAgent, Reply, decision_command

ALLOW_ONCE = "allow-once"
ALLOW_ALWAYS = "allow-always"


def permission_menu() -> list[PermissionOption]:
    return [
        PermissionOption(kind=ALLOW_ONCE_KIND, optionId=ALLOW_ONCE, name="Allow once"),
        PermissionOption(
            kind=ALLOW_ALWAYS_KIND, optionId=ALLOW_ALWAYS, name="Allow always"
        ),
    ]


def permission_selections(agent: FakeACPAgent) -> list[str | None]:
    return [
        response.outcome.option_id
        if isinstance(response.outcome, AllowedOutcome)
        else None
        for response in agent.permission_responses
    ]


def advertised_decisions(reply: Reply) -> dict[CursorCommandWord, str]:
    return {
        command.word: command.token
        for text in reply.texts
        if (command := decision_command(text)) is not None
    }
