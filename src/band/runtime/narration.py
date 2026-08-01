"""The room-event body every adapter's tool narration is read back from.

A ``tool_call`` / ``tool_result`` event carries its detail as a JSON body, and
consumers — the desktop room view, the baseline E2E toolkit, anything reading a
transcript — parse that one shape whatever produced it. Build it here instead of
re-typing the keys per adapter, where one that spells them differently silently
becomes unreadable rather than failing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def tool_call_content(
    name: str,
    *,
    args: Mapping[str, Any] | str | None = None,
    tool_call_id: str | None = None,
) -> str:
    """The body of a ``tool_call`` room event.

    ``args`` may arrive as the JSON string some frameworks hand back (pydantic-ai,
    letta); it is passed through as-is rather than guessed at, and readers
    normalize both forms. Values a framework leaves unserializable are rendered
    with ``str`` instead of failing the narration.
    """
    return json.dumps(
        {
            "name": name,
            "args": args if isinstance(args, str) else dict(args or {}),
            "tool_call_id": tool_call_id,
        },
        default=str,
    )


def tool_result_content(
    name: str,
    *,
    output: Any,
    tool_call_id: str | None = None,
    is_error: bool | None = None,
) -> str:
    """The body of a ``tool_result`` room event.

    ``is_error`` is omitted when unknown — absent means "not reported", which a
    reader must not confuse with a successful result.
    """
    payload: dict[str, Any] = {
        "name": name,
        "output": output,
        "tool_call_id": tool_call_id,
    }
    if is_error is not None:
        payload["is_error"] = is_error
    return json.dumps(payload, default=str)
