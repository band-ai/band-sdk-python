from __future__ import annotations

import warnings

import pytest

from band.adapters.agno import AgnoAdapter


class TestDetection:
    async def test_warns_and_flags_when_db_and_history_enabled(self, make_agno_agent):
        agent = make_agno_agent(add_history_to_context=True, db=object())
        adapter = AgnoAdapter(agent=agent)

        # Detection runs against the runtime agent at startup, not in __init__.
        with pytest.warns(UserWarning, match="manages its own conversation history"):
            await adapter.on_started("TestBot", "desc")

        assert adapter._agno_manages_history is True

    @pytest.mark.parametrize(
        ("add_history_to_context", "db"),
        [
            (True, None),  # history flag but no db -> Agno loads nothing
            (False, object()),  # db but flag off
            (False, None),  # neither
        ],
    )
    async def test_no_guard_unless_both_set(
        self, make_agno_agent, add_history_to_context, db
    ):
        agent = make_agno_agent(add_history_to_context=add_history_to_context, db=db)
        adapter = AgnoAdapter(agent=agent)

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any history warning would fail here
            await adapter.on_started("TestBot", "desc")

        assert adapter._agno_manages_history is False
