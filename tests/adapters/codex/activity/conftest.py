from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from band.client.rest import AsyncRestClient
from band.testing.activity import ActivityRecorder
from tests.runtime.activityownership.kit import ActivityPlatform
from tests.runtime.helpers import rest_client_over


@pytest.fixture
def recorder() -> ActivityRecorder:
    return ActivityRecorder()


@pytest_asyncio.fixture(loop_scope="function")
async def native_platform() -> AsyncIterator[tuple[ActivityPlatform, AsyncRestClient]]:
    platform = ActivityPlatform()
    async with rest_client_over(platform.answer) as rest:
        yield platform, rest
