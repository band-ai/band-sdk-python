"""Guard the one test convention whose breach is invisible: sys.modules surgery.

Evicting a module from ``sys.modules`` does not undo an import — the module object
lives on in every class and function already taken from it, but its name no longer
resolves. Pydantic looks a model's namespace up through ``cls.__module__``, so a
model class from an evicted module can no longer resolve its own annotations, and
building it fails with "... is not fully defined; you should define <name>". The
crewai tool models hit exactly that: tests evicted ``band.integrations.crewai.*``
to fake the package and never put it back, so a later test using the real package
died — but only when it happened to run after one of them, which is why CI stayed
green for so long.

``monkeypatch.setitem`` and ``patch.dict(sys.modules, ...)`` both restore what they
replaced, so there is no reason for a test to reach for a raw pop.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.paths import REPO_ROOT

_TESTS_ROOT = REPO_ROOT / "tests"

# This file is exempt: it carries the pattern below as data to match against.
_THIS_FILE = Path(__file__).resolve()

_RAW_EVICTION = re.compile(r"sys\.modules\.pop\(|del\s+sys\.modules\[")


def _collected_test_sources() -> list[Path]:
    """Every file pytest executes: the test modules and the conftests around them.

    A conftest is the worse place for an eviction — it runs for a whole directory
    — so scanning only ``test_*.py`` would miss the bigger blast radius.
    """
    return [
        path
        for pattern in ("test_*.py", "conftest.py")
        for path in _TESTS_ROOT.rglob(pattern)
    ]


def test_no_test_evicts_modules_from_sys_modules() -> None:
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in _collected_test_sources()
        if path.resolve() != _THIS_FILE
        and _RAW_EVICTION.search(path.read_text(encoding="utf-8"))
    )
    assert not offenders, (
        "these tests evict modules from sys.modules without restoring them, which "
        "breaks annotation resolution for anything that later imports the module "
        f"for real — use monkeypatch.setitem or patch.dict instead: {offenders}"
    )


def test_the_guard_can_actually_see_an_eviction() -> None:
    """Guard the guard: a pattern that stops matching would pass vacuously."""
    assert _RAW_EVICTION.search('sys.modules.pop("band.adapters.crewai", None)')
    assert _RAW_EVICTION.search("del sys.modules[name]")
    assert not _RAW_EVICTION.search('monkeypatch.setitem(sys.modules, "crewai", m)')
