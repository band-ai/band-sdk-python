"""Bounded integer-only protocol JSON shared by portable HTTP clients."""

from __future__ import annotations

import json
from typing import Any

from pydantic import JsonValue

from band.contracts.errors import ContractCapacityError, ContractProtocolError
from remote_agent.protocol import decode_journal as decode_native_journal

JsonObject = dict[str, JsonValue]
MAX_WIRE_BYTES = 4_194_304
MAX_AUTHOR_BYTES = 1_048_576
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def validate_json(value: Any) -> None:
    """Reject values that cannot round-trip through the native protocol."""
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            if depth >= 128:
                raise ContractProtocolError("Contract JSON nesting limit exceeded")
            if isinstance(item, dict):
                if not all(isinstance(key, str) for key in item):
                    raise ContractProtocolError("Contract JSON keys must be strings")
                pending.extend((key, depth + 1) for key in item)
                pending.extend((child, depth + 1) for child in item.values())
            else:
                pending.extend((child, depth + 1) for child in item)
        elif item is None or isinstance(item, bool):
            pass
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise ContractProtocolError(
                    "Contract JSON contains invalid text"
                ) from None
        elif type(item) is int and abs(item) <= MAX_SAFE_INTEGER:
            pass
        else:
            raise ContractProtocolError("Contract JSON requires safe integer numbers")


def encode_json(value: JsonObject, *, limit: int = MAX_WIRE_BYTES) -> bytes:
    """Snapshot the production JSON serializer before delivery or retry."""
    validate_json(value)
    encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    if len(encoded) > limit:
        raise ContractCapacityError("request")
    return encoded


def unique_pairs(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ContractProtocolError("Contract JSON contains a duplicate key")
        result[key] = value
    return result


def decode_json(encoded: bytes) -> JsonObject:
    """Decode an object while preserving the native JSON constraints."""
    try:
        value = json.loads(encoded, object_pairs_hook=unique_pairs)
        validate_json(value)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractProtocolError(
            "Contract response is not valid protocol JSON"
        ) from None
    if not isinstance(value, dict):
        raise ContractProtocolError("Contract response must be an object")
    return value


def decode_journal(encoded: bytes) -> JsonObject:
    """Decode a bounded private journal response with the native value policy."""
    try:
        value = decode_native_journal(encoded)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractProtocolError(
            "Contract response is not valid journal JSON"
        ) from None
    if not isinstance(value, dict):
        raise ContractProtocolError("Contract response must be an object")
    return value
