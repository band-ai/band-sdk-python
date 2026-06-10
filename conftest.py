"""Markdown doc snippet tests — thin entry point."""

from __future__ import annotations

import pytest

from tests.markdown_docs.globals import build_globals

pytest_plugins = ["tests.markdown_docs.fixtures"]


def pytest_markdown_docs_globals() -> dict[str, object]:
    return build_globals()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("markdowndocs", default=False):
        return

    for item in items:
        if item.get_closest_marker("markdown-docs"):
            item.add_marker(pytest.mark.filterwarnings("ignore::DeprecationWarning"))
