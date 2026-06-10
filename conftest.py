"""Markdown doc snippet tests — thin entry point."""

from __future__ import annotations

import pytest

from tests.markdown_docs.globals import build_globals
from tests.markdown_docs.hooks import suppress_deprecation_warnings

pytest_plugins = ["tests.markdown_docs.fixtures"]


def pytest_markdown_docs_globals() -> dict[str, object]:
    return build_globals()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    suppress_deprecation_warnings(config, items)
