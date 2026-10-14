"""Tests for Cursor-specific extension handling via ACP client profiles."""

from __future__ import annotations

import pytest

from band.integrations.acp.client_profiles import CursorACPClientProfile
from band.integrations.acp.client_runtime import ACPCollectingClient


class TestACPCollectingClientCursorProfileExtensions:
    """Tests for Cursor-specific extension handling via ACP client profiles."""

    @pytest.mark.asyncio
    async def test_ext_method_cursor_ask_question(self) -> None:
        """Forwards Cursor's complete question payload to the decision bridge."""
        received: dict[str, object] = {}

        async def resolve(method: str, params: dict[str, object]) -> dict[str, object]:
            received.update(method=method, params=params)
            return {
                "outcome": {
                    "outcome": "answered",
                    "answers": [{"questionId": "q1", "selectedOptionIds": ["a"]}],
                }
            }

        client = ACPCollectingClient(profile=CursorACPClientProfile(resolve))

        result = await client.ext_method(
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

        assert received["method"] == "cursor/ask_question"
        assert result["outcome"]["outcome"] == "answered"

    @pytest.mark.asyncio
    async def test_ext_method_without_decision_bridge_cancels_when_unanswerable(
        self,
    ) -> None:
        """A bare profile (e.g. the generic ACP bridge) has nothing to pick
        from an empty question list, and must still cancel rather than
        fabricate an answer."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        result = await client.ext_method("cursor/ask_question", {"questions": []})

        assert result == {"outcome": {"outcome": "cancelled"}}

    @pytest.mark.asyncio
    async def test_ext_method_without_decision_bridge_auto_answers_unattended(
        self,
    ) -> None:
        """Regression: resolve_acp_client_profile("cursor") (the generic ACP
        bridge's factory) has no room to relay a decision to and no resolver
        -- it must answer unattended (main's prior behavior) rather than
        cancelling every real question and plan outright."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        ask_result = await client.ext_method(
            "cursor/ask_question",
            {
                "questions": [
                    {
                        "id": "q1",
                        "prompt": "Choose",
                        "options": [
                            {"id": "a", "label": "A"},
                            {"id": "b", "label": "B"},
                        ],
                    }
                ],
            },
        )
        plan_result = await client.ext_method("cursor/create_plan", {"plan": "stuff"})

        assert ask_result == {
            "outcome": {
                "outcome": "answered",
                "answers": [{"questionId": "q1", "selectedOptionIds": ["a"]}],
            }
        }
        assert plan_result == {"outcome": {"outcome": "accepted"}}

    @pytest.mark.asyncio
    async def test_ext_method_cursor_create_plan(self) -> None:
        """Forwards plan approval to the decision bridge."""

        async def resolve(method: str, params: dict[str, object]) -> dict[str, object]:
            del method, params
            return {"outcome": {"outcome": "accepted"}}

        client = ACPCollectingClient(profile=CursorACPClientProfile(resolve))

        result = await client.ext_method("cursor/create_plan", {"plan": "stuff"})

        assert result == {"outcome": {"outcome": "accepted"}}

    @pytest.mark.asyncio
    async def test_ext_method_unknown_returns_empty(self) -> None:
        """Should return empty dict for unknown extension methods."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        result = await client.ext_method("unknown/method", {})

        assert result == {}

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_update_todos(self) -> None:
        """Cursor's merge flag preserves prior todo state."""
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [
                    {"id": "read", "content": "Read code", "status": "completed"}
                ],
                "merge": False,
            },
        )
        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [
                    {"id": "test", "content": "Write tests", "status": "pending"}
                ],
                "merge": True,
            },
        )

        chunks = client.get_collected_chunks("sess-1")
        assert "[x] Read code" in chunks[-1].content
        assert "[ ] Write tests" in chunks[-1].content

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_update_todos_clearing_the_list_still_renders(
        self,
    ) -> None:
        """Regression: a `todos: []` update that legitimately clears the list
        used to emit no chunk at all, leaving the room showing the stale
        checklist from before the clear."""
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [
                    {"id": "read", "content": "Read code", "status": "completed"}
                ],
                "merge": False,
            },
        )
        await client.ext_notification(
            "cursor/update_todos",
            {"todos": [], "merge": False},
        )

        chunks = client.get_collected_chunks("sess-1")
        assert len(chunks) == 2
        assert profile._todos_by_session["sess-1"] == {}
        assert chunks[-1].content != chunks[0].content

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_todos_do_not_cross_sessions(self) -> None:
        active = "first"
        profile = CursorACPClientProfile(active_session=lambda: active)
        client = ACPCollectingClient(profile=profile)
        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [{"id": "old", "content": "Old task", "status": "pending"}],
                "merge": False,
            },
        )
        active = "second"
        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [{"id": "new", "content": "New task", "status": "pending"}],
                "merge": True,
            },
        )

        chunks = client.get_collected_chunks("second")
        assert chunks[-1].content == "- [ ] New task"

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_task(self) -> None:
        """Renders documented task metadata without inventing a result field."""
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification(
            "cursor/task",
            {
                "description": "Explore authentication",
                "prompt": "Find the auth module",
                "subagentType": "explore",
                "model": "Auto",
            },
        )

        chunks = client.get_collected_chunks("sess-1")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "plan"
        assert "Explore authentication" in chunks[0].content

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_task_without_a_description_is_a_noop(
        self,
    ) -> None:
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification("cursor/task", {"description": ""})

        assert client.get_collected_chunks("sess-1") == []

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_generate_image_without_a_description_is_a_noop(
        self,
    ) -> None:
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification(
            "cursor/generate_image", {"filePath": "/tmp/logo.png"}
        )

        assert client.get_collected_chunks("sess-1") == []

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_update_todos_marks_in_progress_and_cancelled(
        self,
    ) -> None:
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [
                    {"id": "a", "content": "Working", "status": "in_progress"},
                    {"id": "b", "content": "Dropped", "status": "cancelled"},
                ],
                "merge": False,
            },
        )

        chunks = client.get_collected_chunks("sess-1")
        assert "[~] Working" in chunks[-1].content
        assert "[-] Dropped" in chunks[-1].content

    @pytest.mark.asyncio
    async def test_ext_notification_uses_serialized_profile_session(self) -> None:
        """Cursor notifications omit session ids, so the profile names the turn's."""
        profile = CursorACPClientProfile(active_session=lambda: "sess-1")
        client = ACPCollectingClient(profile=profile)

        await client.ext_notification(
            "cursor/generate_image",
            {"description": "A logo", "filePath": "/tmp/logo.png"},
        )

        chunks = client.get_collected_chunks("sess-1")
        assert chunks[0].content == "[Cursor generated image] A logo → /tmp/logo.png"

    @pytest.mark.asyncio
    async def test_ext_notification_without_bound_session_or_own_id_is_noop(
        self,
    ) -> None:
        """Nothing identifies which session's todos these are."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        await client.ext_notification(
            "cursor/update_todos",
            {
                "todos": [{"id": "test", "content": "Test", "status": "pending"}],
                "merge": False,
            },
        )

        assert client.get_collected_chunks() == []

    @pytest.mark.asyncio
    async def test_ext_notification_todos_use_their_own_session_id_unbound(
        self,
    ) -> None:
        """Regression: the bridge path (resolve_acp_client_profile("cursor"))
        sets no active session, so a notification that carries its own
        sessionId must still render -- not fall silent because self._session_id
        is None. ACPCollectingClient.ext_notification already resolves the
        SAME precedence for chunk routing; the profile's own todo state must
        match it."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        await client.ext_notification(
            "cursor/update_todos",
            {
                "sessionId": "bridge-session",
                "todos": [{"id": "test", "content": "Test", "status": "pending"}],
                "merge": False,
            },
        )

        chunks = client.get_collected_chunks("bridge-session")
        assert chunks[-1].content == "- [ ] Test"
