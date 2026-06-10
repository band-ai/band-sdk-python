from __future__ import annotations

import pytest


def markdown_docs_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("markdowndocs", default=False))


def suppress_deprecation_warnings(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Suppress expected DeprecationWarnings in markdown doc snippet tests."""
    if not markdown_docs_enabled(config):
        return

    for item in items:
        if item.get_closest_marker("markdown-docs"):
            item.add_marker(pytest.mark.filterwarnings("ignore::DeprecationWarning"))
