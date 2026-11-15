"""Distinguish room-stop acknowledgements from activity request failures."""

from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from band.platform.message_lifecycle import MessageLifecycle
from tests.runtime.helpers import rest_client_over
from tests.toolkit.clock import binary_clock_resolution

# Exercise the generated retry policy without waiting on wall time.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


class TestActivityAcknowledgement:
    @pytest.mark.parametrize(
        "working,recorded,stopped",
        [(True, False, True), (True, True, False), (False, False, False)],
    )
    async def test_activity_acknowledgement_separates_stop_from_request_success(
        self, working: bool, recorded: bool, stopped: bool
    ) -> None:
        observed = Mock()
        async with rest_client_over(
            lambda _: httpx.Response(200, json={"data": {"working": recorded}})
        ) as rest:
            assert (
                await MessageLifecycle().report_activity(
                    rest, "room-1", working, on_stopped=observed
                )
                is True
            )
        assert observed.called is stopped

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(401),
            httpx.Response(403),
            httpx.Response(404),
            httpx.Response(503),
            httpx.Response(200, json={"data": {}}),
            httpx.Response(200, json={"data": None}),
        ],
    )
    async def test_activity_failure_is_not_stop(self, response: httpx.Response) -> None:
        observed = Mock()
        async with rest_client_over(lambda _: response) as rest:
            assert (
                await MessageLifecycle().report_activity(
                    rest, "room-1", True, on_stopped=observed
                )
                is False
            )
        observed.assert_not_called()

    async def test_activity_timeout_is_not_stop(self) -> None:
        observed = Mock()

        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("activity timeout", request=request)

        async with rest_client_over(timeout) as rest:
            assert (
                await MessageLifecycle().report_activity(
                    rest, "room-1", True, on_stopped=observed
                )
                is False
            )
        observed.assert_not_called()
