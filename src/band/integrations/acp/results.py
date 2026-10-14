"""Readable ACP tool results and streamed result replacement."""

from __future__ import annotations

import json

from band.integrations.acp.types import ACPToolResult, CollectedChunk


def _strict_json_equal(a: object, b: object) -> bool:
    """Compare JSON types strictly; Python equates booleans and numbers."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            _strict_json_equal(value, b[key]) for key, value in a.items()
        )
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_strict_json_equal(x, y) for x, y in zip(a, b))
    return a == b


def _is_echo_of(content: str, readable: str, echo: dict[str, object]) -> bool:
    """Recognize a readable value followed by an exact JSON echo."""
    if not content.startswith(readable):
        return False
    trailing = content[len(readable) :].strip()
    try:
        return _strict_json_equal(json.loads(trailing), echo)
    except json.JSONDecodeError:
        return False


def _readable_rendering(content: str, structured: dict[str, object]) -> str | None:
    """Find the non-empty rendering of MCP structured content."""
    result = structured.get("result")
    if set(structured) == {"result"} and isinstance(result, str):
        return result if result and content.startswith(result) else None
    try:
        leading_value, end = json.JSONDecoder().raw_decode(content)
    except json.JSONDecodeError:
        return None
    return content[:end] if _strict_json_equal(leading_value, structured) else None


def unwrap_structured_result(
    content: str, raw_output: object
) -> tuple[str, dict[str, object]] | None:
    """Remove only a proven duplicate of MCP structured content."""
    if not isinstance(raw_output, dict):
        return None
    structured = raw_output.get("structuredContent")
    if not isinstance(structured, dict):
        return None
    readable = _readable_rendering(content, structured)
    if readable is not None and _is_echo_of(content, readable, structured):
        return readable, structured
    return None


def fold_result(canonical: CollectedChunk, chunk: CollectedChunk) -> None:
    """Preserve reported status and readable output across result frames."""
    incoming_status = chunk.metadata.get("status")
    if incoming_status is not None:
        canonical.metadata["status"] = incoming_status
    if canonical.echo is not None and _is_echo_of(
        chunk.content, canonical.content, canonical.echo
    ):
        best = canonical
    elif chunk.content and not chunk.from_raw:
        best = chunk
    elif canonical.content and not canonical.from_raw:
        best = canonical
    else:
        best = max(canonical, chunk, key=_result_key)
    canonical.content, canonical.from_raw, canonical.echo = (
        best.content,
        best.from_raw,
        best.echo,
    )
    if isinstance(canonical.tool, ACPToolResult):
        canonical.tool.output = canonical.content
        canonical.tool.status = canonical.metadata.get("status")


def _result_key(chunk: CollectedChunk) -> tuple[bool, bool, int]:
    """Rank fallback frames by readable content and completeness."""
    has_text = bool(chunk.content)
    return has_text, has_text and not chunk.from_raw, len(chunk.content)
