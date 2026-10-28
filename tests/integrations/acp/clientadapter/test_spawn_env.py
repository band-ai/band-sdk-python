"""A backend can give each room's agent process its own environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integrations.acp.acp_toolkit.harness import launch_for
from tests.integrations.acp.clientadapter.kit import (
    CODEX,
    ROOM_ENV_VAR,
    RoomEnvAdapter,
)


class TestSpawnEnv:
    @pytest.mark.asyncio
    async def test_each_room_launches_with_the_env_its_backend_derives_for_it(
        self, tmp_path: Path
    ) -> None:
        adapter = RoomEnvAdapter(
            CODEX.model_copy(update={"cwd": str(tmp_path), "env": {"API_KEY": "k"}})
        )

        launches = [await launch_for(adapter, room) for room in ("room-a", "room-b")]

        assert [launch.env for launch in launches] == [
            {"API_KEY": "k", ROOM_ENV_VAR: "room-a"},
            {"API_KEY": "k", ROOM_ENV_VAR: "room-b"},
        ]
