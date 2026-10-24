from __future__ import annotations

import pytest

from band.testing.activity import ActivityRecorder


@pytest.fixture
def recorder() -> ActivityRecorder:
    return ActivityRecorder()
