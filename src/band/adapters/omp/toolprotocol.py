"""Build host-tool frames and parse native approval titles."""

from __future__ import annotations

import json
from typing import cast

from pydantic import JsonValue

from band.core.types import PERMISSION_CANCELLED_TEXT
from band.integrations.omp import OMP_DEVICE_CONTENT_KEY, OMP_DEVICE_PATH_KEYS
from band.integrations.ompwire import OmpContent, OmpContentKind, OmpToolResult

PRIVATE_PAYLOAD_UNAVAILABLE = "[private payload unavailable]"

OMP_PERMISSION_TOOL = "omp_permission"

OMP_PERMISSION_CALL_ID_PREFIX = "band-permission:"

OMP_TOOL_APPROVAL_HEADER = "Allow tool: "

_ARGUMENTS_MARKER = "Arguments:"

_ARGUMENTS_UNAVAILABLE = "[arguments unavailable]"

_ORIGIN_PREFIX = "Origin:"

_REASON_PREFIX = "Reason:"

_APPROVAL_FIELD_NAMES = {
    OMP_DEVICE_CONTENT_KEY: OMP_DEVICE_CONTENT_KEY,
    **{key: key for key in OMP_DEVICE_PATH_KEYS},
    **{key.replace("_", " "): key for key in OMP_DEVICE_PATH_KEYS},
}

OMP_PERMISSION_CANCELLED = PERMISSION_CANCELLED_TEXT


def _parse_approval_title(title: str) -> tuple[str, dict[str, JsonValue]] | None:
    """Split a live ``formatApprovalPrompt`` title into tool name and fields."""
    first, separator, body = title.partition("\n")
    if not first.startswith(OMP_TOOL_APPROVAL_HEADER):
        return None
    tool_name = first.removeprefix(OMP_TOOL_APPROVAL_HEADER)
    if not tool_name or tool_name != tool_name.strip():
        return None
    if not separator:
        return tool_name, {}
    arguments: dict[str, JsonValue] = {}
    lines = body.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith((_ORIGIN_PREFIX, _REASON_PREFIX)):
            index += 1
            continue
        if line.startswith(_ARGUMENTS_MARKER):
            encoded = line.partition(_ARGUMENTS_MARKER)[2].lstrip()
            parsed = json.loads(encoded)
            if not isinstance(parsed, dict):
                raise ValueError("OMP approval arguments must be an object")
            arguments.update(cast(dict[str, JsonValue], parsed))
            index += 1
            continue
        label, colon, value = line.partition(":")
        field = _APPROVAL_FIELD_NAMES.get(label.strip().lower())
        if not colon or field is None:
            index += 1
            continue
        parts = [value.lstrip()]
        index += 1
        while index < len(lines):
            nxt = lines[index]
            if nxt.startswith((_ORIGIN_PREFIX, _REASON_PREFIX, _ARGUMENTS_MARKER)):
                break
            next_field = _APPROVAL_FIELD_NAMES.get(
                nxt.partition(":")[0].strip().lower()
            )
            if next_field is not None and ":" in nxt:
                break
            parts.append(nxt)
            index += 1
        arguments[field] = "\n".join(parts).strip("\n")
    return tool_name, arguments


def _omp_host_tool(
    name: JsonValue, description: JsonValue, parameters: JsonValue
) -> dict[str, JsonValue]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "loadMode": "essential",
    }


def _text_tool_result(text: str, *, is_error: bool = False) -> OmpToolResult:
    return OmpToolResult(
        content=[OmpContent(type=OmpContentKind.TEXT, text=text)], is_error=is_error
    )


def _unavailable_permission_arguments(title: str) -> dict[str, JsonValue]:
    header, separator, _encoded = title.partition(_ARGUMENTS_MARKER)
    if separator:
        return {"message": f"{header}{_ARGUMENTS_MARKER} {_ARGUMENTS_UNAVAILABLE}"}
    return {"message": title}
