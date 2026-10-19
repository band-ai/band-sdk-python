"""Shared fixtures for the NOOA adapter tests."""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator

import httpx
import pytest

from band.client.rest import AsyncRestClient
from band.testing import FakeAgentTools
from tests.runtime.helpers import rest_client_over

# NOOA supports Python 3.12 and 3.13 on POSIX only; elsewhere no module in this
# package can import, so none is collected.
if importlib.util.find_spec("nooa") is None:
    collect_ignore_glob = ["test_*.py", "kit.py"]


@pytest.fixture
def tools() -> FakeAgentTools:
    """A fresh, call-tracking Band tool surface for one test."""
    return FakeAgentTools()


BOOTSTRAP_NOTE = "The deployment window is named BLUE-PINE."
ROSTER_PERSON = {"id": "roster-user", "name": "RosterPerson", "type": "User"}


@pytest.fixture
async def history_rest() -> AsyncIterator[AsyncRestClient]:
    def answer(request: httpx.Request) -> httpx.Response:
        match request.method, request.url.path:
            case "GET", path if path.endswith("/participants"):
                return httpx.Response(200, json={"data": [ROSTER_PERSON]})
            case "GET", path if path.endswith("/context"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "earlier-message",
                                "content": BOOTSTRAP_NOTE,
                                "sender_id": "user-456",
                                "sender_type": "User",
                                "sender_name": "Alice",
                                "message_type": "text",
                            }
                        ],
                        "metadata": {"has_more": False, "limit": 50},
                    },
                )
            case "POST", path if path.endswith("/events"):
                return httpx.Response(
                    201, json={"data": {"id": "failure-event", "message_type": "event"}}
                )
            case _:
                raise AssertionError(
                    f"Unexpected request: {request.method} {request.url}"
                )

    async with rest_client_over(answer) as rest:
        yield rest
