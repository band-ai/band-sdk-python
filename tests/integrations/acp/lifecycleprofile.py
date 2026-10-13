"""Registers the OMP, Cursor and Copilot ACP adapters with the common Room
lifecycle suite."""

from __future__ import annotations

from functools import partial
from typing import get_args

from tests.adapters.lifecycle.kit import LifecycleProfile
from tests.integrations.acp.acp_toolkit.lifecycle import ACPProfile, install

PROFILES = [
    LifecycleProfile(
        name=profile,
        install=partial(install, profile),
        # The turn reports the lost connection, then re-raises it.
        lost_turn=ConnectionError,
    )
    for profile in get_args(ACPProfile)
]
