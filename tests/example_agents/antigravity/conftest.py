"""Load the Antigravity example with isolated environment settings."""

from __future__ import annotations

import importlib.util
from types import ModuleType

import pytest

from tests.loaders import load_script_module
from tests.paths import EXAMPLES_ROOT

if importlib.util.find_spec("acp") is None:
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture
def antigravity_example(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_script_module(
        EXAMPLES_ROOT / "acp" / "clients" / "antigravity.py",
        "antigravity_client_example",
    )
    for field in module.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    return module
