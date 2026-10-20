"""The copied preamble-task qualname matches the coroutine Parlant compiles.

Parlant 3.3.2 defines that child on ``AlphaEngine``, so a copy that names
``Engine`` never finds the running task. This skips outside the dev-parlant venv.
"""

from __future__ import annotations

import types

import pytest

from band.adapters.parlant.messages import PREAMBLE_TASK_QUALNAME

engine = pytest.importorskip("parlant.core.engines.alpha.engine")


class TestPreambleTaskIdentity:
    def test_qualname_matches_the_installed_coroutine(self) -> None:
        nested = [
            const.co_qualname
            for const in engine.AlphaEngine._get_preamble_task.__code__.co_consts
            if isinstance(const, types.CodeType) and const.co_name == "preamble_task"
        ]

        assert nested == [PREAMBLE_TASK_QUALNAME]
