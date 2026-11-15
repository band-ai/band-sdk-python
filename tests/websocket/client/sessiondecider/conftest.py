from __future__ import annotations

import pytest

from tests.sessionpolicies import session_policy
from tests.websocket.client.sessiondecider.kit import DeciderProbe


@pytest.fixture
def probe() -> DeciderProbe:
    return DeciderProbe(session_policy())
