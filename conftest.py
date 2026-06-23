"""Root pytest entry point for Markdown code-fence tests."""

from __future__ import annotations

import pytest

from tests.markdown_docs.globals import build_globals

# Root markdown files need these fixtures even though the helpers live under tests/.
pytest_plugins = ["tests.markdown_docs.fixtures"]


@pytest.hookimpl(optionalhook=True)
def pytest_markdown_docs_globals() -> dict[str, object]:
    """Namespace for Markdown code fences; see ```tests/markdown_docs/globals.py```."""
    return build_globals()


def pytest_collection_modify_items(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Silence expected SDK deprecations only during Markdown snippet tests."""
    if not config.getoption("markdowndocs", default=False):
        return

    for item in items:
        if item.get_closest_marker("markdown-docs"):
            item.add_marker(pytest.mark.filterwarnings("ignore::DeprecationWarning"))
