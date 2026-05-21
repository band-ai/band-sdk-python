"""Band SDK public import surface.

The implementation currently lives in the compatibility ``thenvoi`` package so
existing integrations keep working during the Band rename.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from types import ModuleType

import thenvoi as _thenvoi
from thenvoi import *  # noqa: F403


class _BandAliasLoader(importlib.abc.Loader):
    def __init__(self, target_name: str) -> None:
        self._target_name = target_name

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return importlib.import_module(self._target_name)

    def exec_module(self, module: ModuleType) -> None:
        return None


class _BandAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object | None,
        target: object | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith("band."):
            return None
        target_name = f"thenvoi.{fullname[len('band.') :]}"
        target_spec = importlib.util.find_spec(target_name)
        if target_spec is None:
            return None
        spec = importlib.util.spec_from_loader(
            fullname,
            _BandAliasLoader(target_name),
            is_package=target_spec.submodule_search_locations is not None,
        )
        if spec is not None:
            spec.submodule_search_locations = target_spec.submodule_search_locations
        return spec


if not any(isinstance(finder, _BandAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _BandAliasFinder())

__all__ = _thenvoi.__all__
__path__ = _thenvoi.__path__
__version__ = _thenvoi.__version__
