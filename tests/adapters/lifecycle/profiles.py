"""Discover lifecycle profiles from their owning provider suites."""

from __future__ import annotations

import importlib

from tests.adapters.lifecycle.kit import PROFILE_MODULE, LifecycleProfile
from tests.paths import REPO_ROOT


def registered_profiles() -> dict[str, LifecycleProfile]:
    modules = sorted((REPO_ROOT / "tests").rglob(PROFILE_MODULE))
    profiles = (
        profile
        for path in modules
        for profile in importlib.import_module(
            ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        ).PROFILES
    )
    return {profile.name: profile for profile in profiles}
