"""Load the bug-hunting skill's scripts as importable modules.

The scripts live under ``.claude/skills/`` because that is where the skill
runtime reads them from, and they are standalone ``uv run`` entry points — not
part of the ``band`` package, so nothing can import them by module name. Their
tests still belong here, like every other test for code outside ``src/``
(``band-bridge`` -> ``tests/bridge``, ``docker/band_python_kit`` ->
``tests/docker``), so the default ``uv run pytest`` collects them.

Loading lives here rather than in ``conftest.py`` because the probe subprocesses
in ``probes.py`` need the same thing, and there must be one way to do it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from tests.paths import BUG_HUNTING_SCRIPTS


@contextmanager
def loaded_script(name: str) -> Iterator[ModuleType]:
    """Import one script by file path, dropping its ``sys.modules`` alias after."""
    path = BUG_HUNTING_SCRIPTS / f"{name}.py"
    module_name = f"bughunting_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so the module can resolve itself while importing.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)
