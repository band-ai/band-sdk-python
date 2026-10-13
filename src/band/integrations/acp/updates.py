"""Project ACP session-update frames into typed chunks."""

from __future__ import annotations

from collections.abc import Callable

from band.integrations.acp.results import unwrap_structured_result
from band.integrations.acp.types import (
    ACPToolCall,
    ChunkType,
    CollectedChunk,
    ToolStatus,
)


class ACPUpdateParser:
    """Parse updates while preserving backend-specific tool-call overrides."""

    def __init__(
        self, canonicalize_tool_name: Callable[[str], str] | None = None
    ) -> None:
        self._canonicalize_tool_name = canonicalize_tool_name or (lambda name: name)

    def _chunk_from_update(self, update: object) -> CollectedChunk | None:
        """Parse one ACP session update without mutating the chunk buffer."""
        match getattr(update, "session_update", None):
            case "agent_message_chunk":
                return self._text_chunk(update, ChunkType.TEXT)
            case "agent_thought_chunk":
                return self._text_chunk(update, ChunkType.THOUGHT)
            case "tool_call":
                return self._tool_call_chunk(update)
            case "tool_call_update":
                return self._tool_result_chunk(update)
            case "plan":
                entries = getattr(update, "entries", [])
                plan_text = "\n".join(
                    getattr(entry, "content", str(entry)) for entry in entries
                )
                return CollectedChunk(chunk_type=ChunkType.PLAN, content=plan_text)
            case _:
                text = self._extract_text_from_content(update)
                return (
                    CollectedChunk(chunk_type=ChunkType.TEXT, content=text)
                    if text
                    else None
                )

    def _text_chunk(self, update: object, chunk_type: str) -> CollectedChunk:
        return CollectedChunk(
            chunk_type=chunk_type,
            content=self._extract_text_from_content(update),
        )

    def _tool_call_chunk(self, update: object) -> CollectedChunk:
        raw_input = getattr(update, "raw_input", None)
        call = ACPToolCall.from_acp(update, canonicalize=self._canonicalize_tool_name)
        metadata = {
            "tool_call_id": call.tool_call_id,
            "raw_input": raw_input,
            "status": getattr(update, "status", ToolStatus.IN_PROGRESS),
        }
        return CollectedChunk(
            chunk_type=ChunkType.TOOL_CALL,
            content=call.name,
            metadata=metadata,
            tool=call,
        )

    def _tool_result_chunk(self, update: object) -> CollectedChunk:
        tool_call_id = getattr(update, "tool_call_id", "")
        status = getattr(update, "status", ToolStatus.COMPLETED)
        metadata = {
            "tool_call_id": tool_call_id,
            "status": status,
        }
        # Readable blocks take precedence over raw output.
        content = self._extract_text_from_tool_content(getattr(update, "content", None))
        from_raw = not content
        echo: dict[str, object] | None = None
        if from_raw:
            raw_output = getattr(update, "raw_output", "")
            content = str(raw_output) if raw_output else ""
        else:
            # MCP can repeat structured content after the readable result.
            unwrapped = unwrap_structured_result(
                content, getattr(update, "raw_output", None)
            )
            if unwrapped is not None:
                content, echo = unwrapped
        return CollectedChunk(
            chunk_type=ChunkType.TOOL_RESULT,
            content=content,
            metadata=metadata,
            from_raw=from_raw,
            echo=echo,
            tool=self._call_revision(update),
        )

    def _call_revision(self, update: object) -> ACPToolCall | None:
        """The call identity a ``tool_call_update`` revises, when it reports one."""
        if not (getattr(update, "title", None) or getattr(update, "raw_input", None)):
            return None
        return ACPToolCall.from_acp(update, canonicalize=self._canonicalize_tool_name)

    @staticmethod
    def _block_text(block: object) -> str:
        """The ``text`` field of a single ACP content block, else ``""``."""
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        return str(text) if text else ""

    @staticmethod
    def _extract_text_from_content(update: object) -> str:
        return ACPUpdateParser._block_text(getattr(update, "content", None))

    @staticmethod
    def _extract_text_from_tool_content(content: object) -> str:
        """Read inline text only; file edits and terminal references have distinct tags."""
        if not isinstance(content, list):
            return ""
        texts = [
            ACPUpdateParser._block_text(getattr(item, "content", None))
            for item in content
            if getattr(item, "type", None) == "content"
        ]
        return "\n".join(text for text in texts if text)
