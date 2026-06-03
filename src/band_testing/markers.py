"""Skip markers for conditional test execution.

Provides reusable skip marker factories for common test patterns.

Usage:
    from band_testing.markers import skip_without_env, skip_in_ci

    # Skip if environment variable not set
    requires_api = skip_without_env("BAND_API_KEY", "API key not set")

    @requires_api
    def test_api_call():
        ...

    # Skip in CI environment
    @skip_in_ci
    def test_local_only():
        ...
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest


def skip_without_env(env_var: str, reason: str | None = None) -> pytest.MarkDecorator:
    """Create a skip marker that skips if an environment variable is not set.

    Args:
        env_var: The environment variable name to check.
        reason: Custom reason message. Defaults to "{env_var} not set".

    Returns:
        A pytest.mark.skipif decorator.

    Example:
        requires_api = skip_without_env("BAND_API_KEY")

        @requires_api
        def test_needs_api():
            ...
    """
    return pytest.mark.skipif(
        not os.environ.get(env_var),
        reason=reason or f"{env_var} environment variable not set",
    )


def skip_without_envs(
    env_vars: list[str], reason: str | None = None
) -> pytest.MarkDecorator:
    """Create a skip marker that skips if ANY of the environment variables are not set.

    Args:
        env_vars: List of environment variable names to check.
        reason: Custom reason message.

    Returns:
        A pytest.mark.skipif decorator.

    Example:
        requires_multi_agent = skip_without_envs(
            ["BAND_API_KEY", "BAND_API_KEY_2"],
            reason="Both API keys required for multi-agent tests"
        )
    """
    missing = [v for v in env_vars if not os.environ.get(v)]
    default_reason = f"Missing environment variables: {', '.join(env_vars)}"
    return pytest.mark.skipif(
        bool(missing),
        reason=reason or default_reason,
    )


def skip_with_condition(
    condition: bool | Callable[[], bool], reason: str
) -> pytest.MarkDecorator:
    """Create a skip marker with a custom condition.

    Args:
        condition: Boolean or callable that returns a boolean.
                   If True, the test is skipped.
        reason: The reason message for skipping.

    Returns:
        A pytest.mark.skipif decorator.

    Example:
        skip_on_windows = skip_with_condition(
            sys.platform == "win32",
            reason="Not supported on Windows"
        )
    """
    cond = condition() if callable(condition) else condition
    return pytest.mark.skipif(cond, reason=reason)


# Pre-defined markers for common patterns
skip_in_ci = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Skipped in CI environment",
)


def pytest_ignore_collect_in_ci(
    collection_path: str, folder: str = "integration"
) -> bool:
    """Helper for pytest_ignore_collect to skip folders in CI.

    Add this to your conftest.py:

        def pytest_ignore_collect(collection_path):
            from band_testing.markers import pytest_ignore_collect_in_ci
            return pytest_ignore_collect_in_ci(str(collection_path), "integration")

    Args:
        collection_path: The path being collected (from pytest hook).
        folder: The folder name to skip in CI (default: "integration").

    Returns:
        True if the path should be ignored, False otherwise.
    """
    if os.environ.get("CI") == "true":
        if folder in str(collection_path):
            return True
    return False
