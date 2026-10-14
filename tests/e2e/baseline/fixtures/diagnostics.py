"""Content-free integration logs for explicitly requested diagnostic runs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.logs import sdk_logs_at

DIAGNOSTIC_PACKAGES = ("band.integrations.parlant", "band.adapters.parlant")


@pytest.fixture(autouse=True)
def diagnostic_logs(baseline_settings: BaselineSettings) -> Iterator[None]:
    with ExitStack() as stack:
        if baseline_settings.run.first_attempt_diagnostics:
            for package in DIAGNOSTIC_PACKAGES:
                stack.enter_context(sdk_logs_at(package, logging.INFO))
        yield
