from __future__ import annotations

from pathlib import Path

import pytest
from bandpap.routing import Grants
from bandpap.storage import StateConflict, StateStore

from tests.pap.adapterkit import grant


class TestPAPGrants:
    def test_existing_binding_cannot_select_another_downstream_connection(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "state")
        grants = Grants(store)
        first = grant()
        grants.provision(first)
        second = grant(
            id="other",
            connection="other",
            route=first.route.model_copy(
                update={"grant": "other", "source": "dot", "recipient": "dot"}
            ),
        )
        with pytest.raises(StateConflict):
            grants.provision(second)
        assert store.db.execute("SELECT COUNT(*) FROM connections").fetchone()[0] == 1
        assert (
            store.conversation(first.session, first.binding).connection
            == first.connection
        )

    def test_roster_projection_never_includes_ungranted_participants(
        self, tmp_path: Path
    ) -> None:
        grants = Grants(StateStore(tmp_path / "state"))
        grants.provision(grant())
        projection = grants.roster(
            "grant",
            [
                {"id": "muse", "name": "Muse", "private": "secret"},
                {"id": "target", "name": "Target"},
                {"id": "bestbuy", "name": "Best Buy"},
            ],
        )
        assert projection == (
            {"id": "muse", "name": "Muse"},
            {"id": "target", "name": "Target"},
        )

    def test_revoked_id_cannot_resurrect_after_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "state"
        first = Grants(StateStore(path))
        first.provision(grant())
        first.revoke("grant")
        second = Grants(StateStore(path))
        with pytest.raises(StateConflict):
            second.provision(grant())
