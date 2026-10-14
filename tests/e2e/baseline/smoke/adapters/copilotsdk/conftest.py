"""Test-owned Copilot homes for bespoke native-runtime scenarios."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.builders import (
    build_copilot_sdk_adapter,
    copilot_home_dir,
)


@pytest.fixture
def copilot_adapter(
    baseline_settings: BaselineSettings, tmp_path: Path
) -> Callable[..., CopilotSDKAdapter]:
    return partial(
        build_copilot_sdk_adapter,
        baseline_settings,
        base_directory=copilot_home_dir(str(tmp_path)),
    )
