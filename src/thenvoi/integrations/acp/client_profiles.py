"""Runtime-specific ACP client profiles."""

from __future__ import annotations

import logging
from typing import Protocol

from thenvoi.integrations.acp.types import CollectedChunk

logger = logging.getLogger(__name__)


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

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        logger.debug("Cursor ACP ext_method: %s, params=%s", method, params)

        if method == "cursor/ask_question":
            options = params.get("options", [])
            if options:
                first = options[0] if isinstance(options, list) else options
                option_id = (
                    first.get("optionId", "0")
                    if isinstance(first, dict)
                    else getattr(first, "optionId", "0")
                )
                return {"outcome": {"type": "selected", "optionId": option_id}}
            return {"outcome": {"type": "cancelled"}}

        if method == "cursor/create_plan":
            return {"outcome": {"type": "approved"}}

        return {}

    async def ext_notification(
        self,
        method: str,
        params: dict[str, object],
    ) -> list[CollectedChunk]:
        logger.debug("Cursor ACP ext_notification: %s, params=%s", method, params)

        if method == "cursor/update_todos":
            todos = params.get("todos", [])
            if todos and isinstance(todos, list):
                lines: list[str] = []
                for todo in todos:
                    if isinstance(todo, dict):
                        done = todo.get("completed", False)
                        text = todo.get("content", "")
                        lines.append(f"- [{'x' if done else ' '}] {text}")
                if lines:
                    return [
                        CollectedChunk(
                            chunk_type="plan",
                            content="\n".join(lines),
                        )
                    ]

        if method == "cursor/task":
            result = str(params.get("result", ""))
            if result:
                return [
                    CollectedChunk(
                        chunk_type="text",
                        content=f"[Task completed] {result}",
                    )
                ]

        return []
