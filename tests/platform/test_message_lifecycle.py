"""Reject lifecycle responses that do not confirm the requested operation."""

from __future__ import annotations

import httpx
import pytest
from band_rest.core.api_error import ApiError
from band_rest.core.parse_error import ParsingError

from band.core.exceptions import RoomExecutionStoppedError
from band.platform.message_lifecycle import MessageLifecycle
from tests.runtime.helpers import rest_client_over
from tests.toolkit.clock import binary_clock_resolution

# Exercise the generated retry policy without waiting on wall time.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


class TestMessageLifecycleResponses:
    @pytest.mark.parametrize(
        "operation,extra",
        [
            ("mark_processing", ()),
            ("mark_processed", ()),
            ("mark_failed", ("failure",)),
        ],
    )
    async def test_stopped_lifecycle_mark_is_refused(
        self, operation: str, extra: tuple[str, ...]
    ) -> None:
        async with rest_client_over(lambda _: httpx.Response(204)) as rest:
            method = getattr(MessageLifecycle(), operation)
            with pytest.raises(RoomExecutionStoppedError):
                await method(rest, "room-1", "msg-1", *extra)

    @pytest.mark.parametrize("status", [200, 401, 403, 404, 503])
    async def test_empty_response_is_not_quiet(self, status: int) -> None:
        async with rest_client_over(lambda _: httpx.Response(status)) as rest:
            with pytest.raises(ApiError):
                await MessageLifecycle().get_next_message(rest, "room-1")

    @pytest.mark.parametrize("status", [200, 401, 403, 404, 503])
    async def test_empty_response_cannot_accept_a_claim(self, status: int) -> None:
        async with rest_client_over(lambda _: httpx.Response(status)) as rest:
            assert not await MessageLifecycle().mark_processing(rest, "room-1", "msg-1")

    async def test_next_204_is_quiet(self) -> None:
        async with rest_client_over(lambda _: httpx.Response(204)) as rest:
            assert await MessageLifecycle().get_next_message(rest, "room-1") is None

    @pytest.mark.parametrize(
        "operation,status,extra",
        [
            ("mark_processing", "processing", ()),
            ("mark_processed", "processed", ()),
            ("mark_failed", "failed", ("failure",)),
        ],
    )
    @pytest.mark.parametrize("success", [True, False])
    async def test_mark_requires_success_data(
        self, operation: str, status: str, extra: tuple[str, ...], success: bool
    ) -> None:
        body = {
            "data": {
                "id": "msg-1",
                "status": status,
                "attempt_number": 1,
                "success": success,
            }
        }
        async with rest_client_over(lambda _: httpx.Response(200, json=body)) as rest:
            method = getattr(MessageLifecycle(), operation)
            assert await method(rest, "room-1", "msg-1", *extra) is success

    async def test_malformed_next_success_is_not_quiet(self) -> None:
        async with rest_client_over(
            lambda _: httpx.Response(200, json={"data": {}})
        ) as rest:
            with pytest.raises(ParsingError):
                await MessageLifecycle().get_next_message(rest, "room-1")
