"""Senders and scripted turns shared by the Claude SDK batch suites."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from band.core.types import PlatformMessage
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

ALICE = {"id": "alice", "name": "Alice"}
HELPER = {"id": "helper", "name": "Helper"}
CAROL = {"id": "carol", "name": "Carol"}
ADMIN = {"id": "admin-1", "name": "Admin"}
LIST_FILES = ModelDecision.call("Bash", command="ls")
DECLINED = "User declined tool use"


def alice_helper_alice(room: ClaudeRoom) -> tuple[PlatformMessage, ...]:
    """The queued originals of one batch: Alice, Helper, then Alice again."""
    return (
        room.message("list the files", sender=ALICE),
        room.message("fyi: the build is green", sender=HELPER),
        room.message("then jot a note", sender=ALICE),
    )
