"""Supply isolated identity, SQLite and external dispatch fixtures."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.paths import TEAMS_DIR

sys.path.insert(0, str(TEAMS_DIR / "src"))
from bandteams.evidence import Journal
from bandteams.proposals import Origin, Proposals
from bandteams.settings import Binding, Room

collect_ignore = ["contracts"]


@pytest.fixture
def person() -> Binding:
    return Binding(
        alias="alice",
        tenant_id="tenant",
        actor_id="alice-entra",
        band_user_id="alice-band",
        team_id="alice-team",
        channel_id="alice-channel",
        group_id="alice-group",
        rooms=(
            Room(id="room-one", recipients=("recipient-one",)),
            Room(id="room-two", recipients=("recipient-two",)),
        ),
        private_owner_verified=True,
        rsc_consent_verified=True,
    )


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[Journal]:
    journal = Journal(tmp_path / "state")
    yield journal
    journal.close()


@pytest.fixture
def proposals(journal: Journal, person: Binding) -> Proposals:
    proposals = Proposals(journal)
    proposals.activate(person)
    return proposals


@pytest.fixture
def origin(person: Binding) -> Origin:
    return Origin(
        tenant=person.tenant_id,
        actor=person.actor_id,
        team=person.team_id,
        channel=person.channel_id,
        conversation="thread",
        activity="native-reply",
    )
