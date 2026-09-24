"""Runtime-specific ACP client profiles."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from band.integrations.acp.types import ChunkType, CollectedChunk

logger = logging.getLogger(__name__)

CursorMethodResolver = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
CURSOR_ASK_QUESTION_METHOD = "cursor/ask_question"
CURSOR_CREATE_PLAN_METHOD = "cursor/create_plan"


class ACPClientProfile(Protocol):
    """Extension hook surface for runtime-specific ACP behavior."""

    @property
    def extension_session_id(self) -> str | None: ...

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

    @property
    def extension_session_id(self) -> None:
        return None

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
        self._todos_by_session: dict[str, dict[str, tuple[str, str]]] = {}

    @property
    def extension_session_id(self) -> str | None:
        """The session receiving Cursor notifications without a session id."""
        return self._session_id

    def bind_session(self, session_id: str | None) -> None:
        """Bind extension notifications to the serialized Cursor turn's session."""
        self._session_id = session_id

    def forget_session(self, session_id: str) -> None:
        """Drop todo state when the adapter releases its ACP session."""
        self._todos_by_session.pop(session_id, None)

    def clear_sessions(self) -> None:
        """Drop all todo state during adapter-wide teardown."""
        self._todos_by_session.clear()

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        logger.debug("Cursor ACP extension method: %s", method)
        if method not in {CURSOR_ASK_QUESTION_METHOD, CURSOR_CREATE_PLAN_METHOD}:
            return {}
        if self._resolve_method is not None:
            return await self._resolve_method(method, params)
        # No resolver means this profile is standalone (e.g. the generic ACP
        # bridge), with no adapter-owned room to relay a decision to --
        # answer unattended rather than cancelling every request outright.
        if method == CURSOR_ASK_QUESTION_METHOD:
            return self._auto_answer_question(params)
        return {"outcome": {"outcome": "accepted"}}

    @staticmethod
    def _auto_answer_question(params: dict[str, object]) -> dict[str, object]:
        """Pick each question's first advertised option, unattended."""
        questions = params.get("questions")
        if not isinstance(questions, list):
            return {"outcome": {"outcome": "cancelled"}}
        answers: list[dict[str, object]] = []
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            question_id, options = question.get("id"), question.get("options")
            if not isinstance(question_id, str) or not isinstance(options, list):
                continue
            first_option_id = next(
                (
                    option_id
                    for option in options
                    if isinstance(option, Mapping)
                    and isinstance((option_id := option.get("id")), str)
                ),
                None,
            )
            if first_option_id is not None:
                answers.append(
                    {"questionId": question_id, "selectedOptionIds": [first_option_id]}
                )
        if not answers:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "answered", "answers": answers}}

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
        # The bound session covers the adapter-integrated path (a turn binds
        # its session before Cursor can notify); standalone use (e.g. the
        # generic ACP bridge) never binds one, so fall back to the
        # notification's own id -- the same precedence ACPCollectingClient
        # already uses to route this chunk to a transcript.
        session_id = (
            params.get("sessionId") or params.get("session_id") or self._session_id
        )
        if not isinstance(todos, list) or not isinstance(session_id, str):
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
            current_todos = self._todos_by_session.setdefault(session_id, {})
            current_todos.update(updates)
        else:
            self._todos_by_session[session_id] = updates
        current_todos = self._todos_by_session[session_id]
        if not current_todos:
            return []
        marks = {
            "completed": "x",
            "in_progress": "~",
            "cancelled": "-",
            "pending": " ",
        }
        lines = [
            f"- [{marks.get(status, ' ')}] {content}"
            for content, status in current_todos.values()
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


CURSOR_PROFILE_NAME = "cursor"


def resolve_acp_client_profile(profile_name: str) -> ACPClientProfile | None:
    """Map a configured profile name to a runtime-specific ACP client profile."""
    normalized = profile_name.strip().lower()
    if not normalized:
        return None
    if normalized == CURSOR_PROFILE_NAME:
        return CursorACPClientProfile()
    return None
