"""Import the release-docs skill's scripts by their plain module names.

The scripts are standalone ``uv run --script`` files that import their siblings
by plain name, which Python resolves from the script's own directory. Tests get
the same resolution by putting that directory first on ``sys.path`` for the
duration of one test.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from tests.paths import RELEASE_DOCS_SCRIPTS


def is_script_module(module: ModuleType) -> bool:
    origin = getattr(module, "__file__", None)
    return origin is not None and Path(origin).parent == RELEASE_DOCS_SCRIPTS


@contextmanager
def release_scripts() -> Iterator[Callable[[str], ModuleType]]:
    """Yield an importer for the scripts; forget every script module afterwards."""
    sys.path.insert(0, str(RELEASE_DOCS_SCRIPTS))
    try:
        yield importlib.import_module
    finally:
        sys.path.remove(str(RELEASE_DOCS_SCRIPTS))
        loaded = [
            name for name, module in sys.modules.items() if is_script_module(module)
        ]
        for name in loaded:
            del sys.modules[name]
