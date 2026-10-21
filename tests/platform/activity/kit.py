from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

import httpx
import pytest

from band.platform.activity import API_VERSION_HEADER
from band.platform.message_lifecycle import logger as lifecycle_logger
from tests.activitysupport import SUPPORTED_VERSION

DETAIL = {"kind": "tool_call", "name": "Bash"}


def lifecycle_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Records of the delivery logger alone, not the version negotiation's."""
    return [r for r in caplog.records if r.name == lifecycle_logger.name]


class ActivityPeer:
    """The activity endpoint, advertising ``version`` on every response."""

    def __init__(self) -> None:
        self.version: str | None = SUPPORTED_VERSION
        self.reports: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.responses: deque[httpx.Response] = deque()
        self.stall_post_number: int | None = None

    async def respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(request)
        self.reports.append(body)
        if len(self.reports) == self.stall_post_number:
            await asyncio.Event().wait()
        response = (
            self.responses.popleft()
            if self.responses
            else httpx.Response(200, json={"data": {"working": body["working"]}})
        )
        if self.version is not None:
            response.headers[API_VERSION_HEADER] = self.version
        return response
