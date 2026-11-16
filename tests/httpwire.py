"""Decode what a test's HTTP boundary received."""

from __future__ import annotations

import json
from typing import Any

import httpx


def request_json(request: httpx.Request) -> Any:
    """The JSON body ``request`` carried, or None when it had none."""
    return json.loads(request.content) if request.content else None
