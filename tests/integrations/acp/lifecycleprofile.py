"""Registers the Cursor and Copilot ACP adapters with the common Room
lifecycle suite."""

from __future__ import annotations

from functools import partial
from typing import get_args

from acp import RequestError

from tests.adapters.lifecycle.kit import ExpectedFailure, LifecycleProfile
from tests.adapters.roompeer import STARTUP_FAILURE
from tests.integrations.acp.acp_toolkit.lifecycle import ACPProfile, install

PROFILES = [
    LifecycleProfile(
        name=profile,
        install=partial(install, profile),
        # The turn reports the lost connection, then re-raises it.
        lost_turn=ConnectionError,
        failed_start=ExpectedFailure(RequestError, STARTUP_FAILURE),
    )
    for profile in get_args(ACPProfile)
]
