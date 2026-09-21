"""Runtime-specific ACP client profiles."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from band.integrations.acp.types import ChunkType, CollectedChunk

logger = logging.getLogger(__name__)

CursorMethodResolver = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class ACPClientProfile(Protocol):
    """Extension hook surface for runtime-specific ACP behavior."""

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]: ...

    async def ext_notification(
        self,
        method: str,
        params: dict[str, object],
    ) -> list[CollectedChunk]: ...


class NoopACPClientProfile:
    """Default profile that ignores ACP extension methods and notifications."""

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        del method, params
        return {}

    async def ext_notification(
        self,
        method: str,
        params: dict[str, object],
    ) -> list[CollectedChunk]:
        del method, params
        return []


class CursorACPClientProfile:
    """Cursor-specific ACP extension handling."""

    def __init__(self, resolve_method: CursorMethodResolver | None = None) -> None:
        self._resolve_method = resolve_method
        self._session_id: str | None = None
        self._todos: dict[str, tuple[str, str]] = {}

    @property
    def extension_session_id(self) -> str | None:
        """The session receiving Cursor notifications without a session id."""
        return self._session_id

    def bind_session(self, session_id: str | None) -> None:
        """Bind extension notifications to the serialized Cursor turn's session."""
        self._session_id = session_id

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        logger.debug("Cursor ACP extension method: %s", method)
        if method not in {"cursor/ask_question", "cursor/create_plan"}:
            return {}
        if self._resolve_method is None:
            return {"outcome": {"outcome": "cancelled"}}
        return await self._resolve_method(method, params)

    async def ext_notification(
        self,
        method: str,
        params: dict[str, object],
    ) -> list[CollectedChunk]:
        logger.debug("Cursor ACP extension notification: %s", method)

        if method == "cursor/update_todos":
            return self._todo_chunks(params)

        if method == "cursor/task":
            return self._task_chunks(params)

        if method == "cursor/generate_image":
            return self._image_chunks(params)

        return []

    def _todo_chunks(self, params: dict[str, object]) -> list[CollectedChunk]:
        """Apply Cursor's replace-or-merge todo update and render its state."""
        todos = params.get("todos")
        if not isinstance(todos, list):
            return []
        updates = {
            todo_id: (content, status)
            for todo in todos
            if isinstance(todo, dict)
            and isinstance((todo_id := todo.get("id")), str)
            and isinstance((content := todo.get("content")), str)
            and isinstance((status := todo.get("status")), str)
        }
        if params.get("merge") is True:
            self._todos.update(updates)
        else:
            self._todos = updates
        if not self._todos:
            return []
        marks = {
            "completed": "x",
            "in_progress": "~",
            "cancelled": "-",
            "pending": " ",
        }
        lines = [
            f"- [{marks.get(status, ' ')}] {content}"
            for content, status in self._todos.values()
        ]
        return [
            CollectedChunk(
                chunk_type=ChunkType.PLAN,
                content="\n".join(lines),
                metadata={"cursor_todos": True},
            )
        ]

    @staticmethod
    def _task_chunks(params: dict[str, object]) -> list[CollectedChunk]:
        """Render Cursor's documented subagent-task notification."""
        description = params.get("description")
        if not isinstance(description, str) or not description:
            return []
        subagent_type = params.get("subagentType", "unspecified")
        model = params.get("model")
        details = f"[Cursor {subagent_type} task] {description}"
        if isinstance(model, str) and model:
            details = f"{details} ({model})"
        return [CollectedChunk(chunk_type=ChunkType.PLAN, content=details)]

    @staticmethod
    def _image_chunks(params: dict[str, object]) -> list[CollectedChunk]:
        """Render generated-image metadata without reading arbitrary local files."""
        description = params.get("description")
        if not isinstance(description, str) or not description:
            return []
        file_path = params.get("filePath")
        suffix = f" → {file_path}" if isinstance(file_path, str) and file_path else ""
        return [
            CollectedChunk(
                chunk_type=ChunkType.PLAN,
                content=f"[Cursor generated image] {description}{suffix}",
            )
        ]
