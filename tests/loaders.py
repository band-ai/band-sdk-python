"""Import-by-path loading for test subjects that aren't importable modules.

Release helpers live in ``scripts/`` with hyphenated filenames (the repo's
naming rule) and example programs live outside any package, so neither can be
reached with a plain ``import``. Load them by file path for off-runner unit
testing of their logic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from tests.paths import REPO_ROOT


def load_script_module(path: str | Path, module_name: str) -> ModuleType:
    """Load a Python file as a module; relative paths anchor at the repo root.

    ``module_name`` is how the module registers in ``sys.modules`` — pick a
    globally unique one (``arena_prompts``, not ``prompts``): several example
    trees expose same-named top-level modules, and a bare name would collide
    with — or permanently shadow — another test's subject.

    Registers the module in ``sys.modules`` before executing it (the canonical
    importlib recipe) — dataclasses and other reflection resolve a class's
    module through ``sys.modules``, so an unregistered module breaks them.

    The script's own directory is put at ``sys.path[0]`` for the duration of
    the load so sibling imports resolve, and ``sys.path`` is restored afterwards
    — both this insertion and any the script itself makes (example programs
    insert their package root on import) would otherwise reorder the global
    path for the rest of the session and shadow other tests' imports. The
    loaded module's own imports resolve during ``exec_module`` and stay cached
    in ``sys.modules``, so the path is only needed during the load.
    """
    path = Path(path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    saved_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.path[:] = saved_path
    return module
