"""Locally measured author transport limits, distinct from ambiguous HTTP failures."""

from __future__ import annotations

import json

from remote_agent.types import JsonObject


class AuthorCapacityError(ValueError):
    def __init__(self, direction: str) -> None:
        self.reason = direction + "_capacity"
        super().__init__("Author " + direction + " capacity")


def encode_request(body: JsonObject) -> bytes:
    """Use the production serializer for both admission and actual delivery."""
    encoded = json.dumps(body).encode()
    if len(encoded) > 1048576:
        raise AuthorCapacityError("request")
    return encoded
