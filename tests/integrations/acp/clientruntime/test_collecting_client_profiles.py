"""ACP collecting client delegation to its client profile."""

from __future__ import annotations

import pytest

from band.integrations.acp.client_profiles import (
    CursorACPClientProfile,
    NoopACPClientProfile,
)
from band.integrations.acp.client_runtime import ACPCollectingClient
from band.integrations.acp.types import ChunkType, CollectedChunk


class TestACPCollectingClientProfiles:
    """Tests for ACP collecting client profile delegation."""

    @pytest.mark.asyncio
    async def test_noop_profile_ignores_extensions(self) -> None:
        client = ACPCollectingClient(profile=NoopACPClientProfile())

        method_result = await client.ext_method("unknown/method", {})
        await client.ext_notification("unknown/notification", {"sessionId": "sess-1"})

        assert method_result == {}
        assert client.get_collected_chunks("sess-1") == []

    @pytest.mark.asyncio
    async def test_cursor_profile_handles_methods_and_notifications(self) -> None:
        async def resolve(method: str, params: dict[str, object]) -> dict[str, object]:
            del params
            return {
                "outcome": {
                    "outcome": "accepted" if method.endswith("plan") else "cancelled"
                }
            }

        profile = CursorACPClientProfile(resolve, active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        ask_result = await client.ext_method(
            "cursor/ask_question",
            {
                "questions": [
                    {
                        "id": "q1",
                        "prompt": "Choose",
                        "options": [{"id": "a", "label": "A"}],
                    }
                ],
            },
        )
        plan_result = await client.ext_method("cursor/create_plan", {"plan": "x"})
        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [
                    {"id": "read", "content": "Read code", "status": "completed"},
                    {"id": "test", "content": "Write tests", "status": "pending"},
                ],
                "merge": False,
            },
        )
        await client.ext_notification(
            "cursor/task",
            {
                "description": "Refactor the module",
                "prompt": "Do it",
                "subagentType": "explore",
            },
        )

        chunks = client.get_collected_chunks("sess-1")
        assert ask_result == {"outcome": {"outcome": "cancelled"}}
        assert plan_result == {"outcome": {"outcome": "accepted"}}
        assert [chunk.chunk_type for chunk in chunks] == ["plan", "plan"]
        assert "[x] Read code" in chunks[0].content
        assert "Refactor the module" in chunks[1].content

    @pytest.mark.asyncio
    async def test_ext_notification_tolerates_a_profile_without_extension_session_id(
        self,
    ) -> None:
        """A custom ACPClientProfile written before extension_session_id was
        added to the protocol has no such attribute; a session-less
        notification must not raise AttributeError reading it."""

        class LegacyProfile:
            async def ext_method(
                self, method: str, params: dict[str, object]
            ) -> dict[str, object]:
                del method, params
                return {}

            async def ext_notification(
                self, method: str, params: dict[str, object]
            ) -> list[CollectedChunk]:
                del method, params
                return [CollectedChunk(chunk_type=ChunkType.TEXT, content="hi")]

        client = ACPCollectingClient(profile=LegacyProfile())  # type: ignore[arg-type]

        await client.ext_notification("cursor/task", {"description": "d"})

        assert client.get_collected_chunks() == []
