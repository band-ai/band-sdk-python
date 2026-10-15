"""Shared helpers for reconciliation and resync tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
)


class ReconciliationPeer(LifecyclePlatform):
    """Expose polling observations without test-side delays."""

    def __init__(self) -> None:
        super().__init__()
        self._next_read = asyncio.Event()

    def answer(self, request: httpx.Request) -> httpx.Response:
        response = super().answer(request)
        if request.url.path.endswith("/next"):
            self._next_read.set()
        return response

    async def wait_for_reads(self, count: int) -> None:
        while len(self.next_reads) < count:
            self._next_read.clear()
            await self._next_read.wait()


ReconciledRoom = tuple[ExecutionContext, ReconciliationPeer, list[str]]


def make_platform_message(
    msg_id: str = "msg-1", room_id: str = "room-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="Hello",
        sender_id="user-999",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


class BacklogApi:
    """Stateful HTTP peer for ordered recovery through the real REST client."""

    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.statuses: dict[str, str] = {}
        self.transitions: list[tuple[str, str]] = []
        self.next_calls = 0
        self.list_calls = 0
        self.listing_queries: list[dict[str, str]] = []
        self.fixed_head: str | None = None
        self.listing_error: tuple[str, int] | None = None
        self.refuse_processed: set[str] = set()
        self.processing_response: httpx.Response | None = None
        self.pagination = "page"
        self.cursor_behavior = "normal"
        self.reject_page_parameters = False
        self.stopped = False

    def add(self, message_id: str, status: str, *, self_authored: bool = False) -> None:
        created_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
            seconds=len(self.messages)
        )
        self.messages[message_id] = {
            "id": message_id,
            "chat_room_id": ROOM_ID,
            "content": message_id,
            "sender_id": AGENT_ID if self_authored else "user-1",
            "sender_type": "Agent" if self_authored else "User",
            "sender_name": "Agent" if self_authored else "User One",
            "message_type": "text",
            "attachments": [],
            "inserted_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
        }
        self.statuses[message_id] = status

    def message(self, message_id: str) -> dict[str, Any]:
        return {
            **self.messages[message_id],
            "metadata": {
                "mentions": [],
                "delivery_status": {AGENT_ID: {"status": self.statuses[message_id]}},
            },
        }

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/participants"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/activity"):
            working = json.loads(request.content)["working"]
            return httpx.Response(
                200, json={"data": {"working": working and not self.stopped}}
            )
        if path.endswith("/messages/next"):
            self.next_calls += 1
            if self.next_calls > 50:
                raise AssertionError("Repeated /next head did not make progress")
            if self.stopped:
                return httpx.Response(204)
            message_id = self.fixed_head or next(
                (mid for mid, status in self.statuses.items() if status != "processed"),
                None,
            )
            if message_id is None:
                return httpx.Response(204)
            return httpx.Response(200, json={"data": self.message(message_id)})

        if path.endswith("/messages") and request.method == "GET":
            if self.reject_page_parameters and "page" in request.url.params:
                return httpx.Response(400, json={"error": "page pagination retired"})
            self.list_calls += 1
            self.listing_queries.append(dict(request.url.params))
            if self.list_calls > 50:
                raise AssertionError("Cursor listing did not make progress")
            status = request.url.params.get("status", "")
            page = int(
                request.url.params.get("cursor") or request.url.params.get("page", "1")
            )
            if self.listing_error == (status, page):
                return httpx.Response(400, json={"error": "listing unavailable"})
            eligible = (
                {"new", "pending", "delivered", "failed", "processing"}
                if not status
                else {"pending", "delivered", "failed"}
                if status == "pending"
                else {status}
            )
            message_ids = [
                mid for mid, current in self.statuses.items() if current in eligible
            ]
            total_pages = max(1, len(message_ids))
            next_cursor = None
            if self.pagination in {"cursor", "mixed"} and page < total_pages:
                next_cursor = str(page + 1)
                if self.cursor_behavior == "missing":
                    next_cursor = None
                elif self.cursor_behavior == "repeated":
                    next_cursor = "1"
            return httpx.Response(
                200,
                json={
                    "data": [self.message(mid) for mid in message_ids[page - 1 : page]],
                    "metadata": {
                        "page": page if self.pagination == "page" else None,
                        "page_size": 1,
                        "total_count": len(message_ids),
                        "total_pages": (
                            total_pages
                            if self.pagination in {"page", "mixed"}
                            else None
                        ),
                        "limit": 1,
                        "has_more": page < total_pages,
                        "next_cursor": next_cursor,
                    },
                },
            )

        message_id, action = path.rsplit("/", 2)[-2:]
        if request.method == "POST" and action in {
            "processing",
            "processed",
            "failed",
        }:
            if self.stopped:
                return httpx.Response(204)
            if action == "processing" and self.processing_response is not None:
                return self.processing_response
            if action == "processed" and message_id in self.refuse_processed:
                return httpx.Response(400, json={"error": "ack unavailable"})
            self.statuses[message_id] = action
            self.transitions.append((message_id, action))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": message_id,
                        "status": action,
                        "attempt_number": 1,
                        "success": True,
                    }
                },
            )
        raise AssertionError(f"Unexpected HTTP request: {request.method} {path}")
