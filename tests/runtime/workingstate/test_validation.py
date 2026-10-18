from __future__ import annotations

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingstate.kit import Recorder


class TestValidation:
    def test_non_positive_cadence_rejected(self):
        with pytest.raises(ValueError):
            WorkingStateReporter(Recorder(), keep_alive_seconds=0)
