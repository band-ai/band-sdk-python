"""Memory-test fixture: a per-test ``MemoryProbe`` that cleans up on teardown."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from band_rest import AsyncRestClient

from tests.conftest_integration import is_no_clean_mode
from tests.e2e.conftest import E2ESettings
from tests.e2e.helpers import MemoryProbe


@pytest.fixture
async def memory(
    e2e_session_client: AsyncRestClient,
    e2e_config: E2ESettings,
    request: pytest.FixtureRequest,
) -> AsyncGenerator[MemoryProbe, None]:
    """Memory-test toolkit: ``memory.marker(...)`` + ``await memory.wait(...)``.

    Archives whatever it matched on teardown (skipped under ``--no-clean`` /
    ``BAND_TEST_NO_CLEAN``). Any memory-capable adapter test can depend on this.
    """
    probe = MemoryProbe(e2e_session_client, default_timeout=e2e_config.e2e_timeout)
    yield probe
    if not is_no_clean_mode(request):
        await probe.archive_all()
