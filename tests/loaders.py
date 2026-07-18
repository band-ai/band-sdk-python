"""Import-by-path loading for test subjects that aren't importable modules.

Release helpers live in ``scripts/`` with hyphenated filenames (the repo's
naming rule) and example programs live outside any package, so neither can be
reached with a plain ``import``. Load them by file path for off-runner unit
testing of their logic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from tests.paths import REPO_ROOT


def load_script_module(path: str | Path, module_name: str) -> ModuleType:
    """Load a Python file as a module; relative paths anchor at the repo root."""
    path = Path(path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
