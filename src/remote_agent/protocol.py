"""Language-independent band-contract canonical JSON; separate from the journal format."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from remote_agent.types import JsonObject

SAFE_INTEGER = 9_007_199_254_740_991
_ESCAPE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')


def _validate(value: object) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= SAFE_INTEGER:
        return
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return
    if isinstance(value, list):
        for item in value:
            _validate(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _validate(key)
            _validate(item)
        return
    raise ValueError("Unsupported protocol JSON value")


def canonical(value: object) -> bytes:
    """UTF-8, sorted keys, integer-only numbers, short escapes and uppercase control hex."""
    _validate(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    # Match complete escape tokens so a literal backslash-u string remains unchanged.
    encoded = _ESCAPE.sub(
        lambda match: (
            "\\u" + match[0][2:].upper() if match[0].startswith("\\u") else match[0]
        ),
        encoded,
    )
    return encoded.encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def check_depth(value: object, *, max_depth: int = 128) -> None:
    """Count container levels, including empty containers; scalars add no level."""
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (list, dict)):
            depth += 1
            if depth > max_depth:
                raise ValueError(f"Protocol JSON nesting exceeds {max_depth} levels")
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth) for child in children)


def _decode(encoded: str | bytes, *, max_depth: int | None) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> JsonObject:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate protocol JSON key")
            result[key] = value
        return result

    value = json.loads(encoded, object_pairs_hook=pairs)
    if max_depth is not None:
        # Keep callback admission independent of host frames and leave room for
        # the journal envelopes that retain accepted values.
        check_depth(value, max_depth=max_depth)
    _validate(value)
    return value


def decode(encoded: str | bytes, *, max_depth: int = 128) -> Any:
    return _decode(encoded, max_depth=max_depth)


def decode_journal(encoded: str | bytes) -> Any:
    """Read private journal JSON within the parser and validator recursion limits."""
    # Private records include runtime envelopes and retained history; callback
    # admission depth is not a journal-format limit. Callers bound input bytes.
    return _decode(encoded, max_depth=None)
