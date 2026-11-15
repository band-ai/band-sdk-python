"""Project canonical ACP task records into native session identities."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path

from band.converters.acp_client import ACP_CLIENT_RESUME_RECORD
from band.core.types import metadata_to_dict
from tests.e2e.baseline.toolkit.observations.events import Events


def room_session_id(tasks: Events, room_id: str) -> str:
    """Require a canonical native session record for this room."""
    candidates = ACP_CLIENT_RESUME_RECORD.candidates(
        {
            "message_type": task.message_type,
            "content": task.content,
            "metadata": metadata_to_dict(task.metadata),
        }
        for task in tasks
    )
    assert room_id in candidates, f"No native session record for room {room_id}"
    return candidates[room_id]


def assert_private_homes(homes: Sequence[Path]) -> None:
    """Check distinct native directories and the host's private-mode contract."""
    from band.adapters.antigravity_acp import (  # noqa: PLC0415 -- optional acp extra
        PRIVATE_HOME_MODE,
    )

    assert len({home.resolve() for home in homes}) == len(homes)
    for home in homes:
        assert home.is_dir()
        assert home.resolve() == home
        # Windows directory modes do not represent ACL privacy.
        if os.name != "nt":
            assert stat.S_IMODE(home.stat().st_mode) == PRIVATE_HOME_MODE
