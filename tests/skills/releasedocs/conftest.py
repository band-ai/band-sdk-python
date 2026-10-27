"""Fixtures for the release-docs skill's script tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.gitrepo import ThrowawayRepo
from tests.skills.releasedocs.samples import (
    PREVIOUS_TAG,
    PRODUCT,
    RELEASE_DATE,
    RELEASE_TAG,
    RELEASE_URL,
)
from tests.skills.releasedocs.scripts import release_scripts
from tests.skills.releasedocs.webhookpeer import WebhookPeer, webhook_peer


@pytest.fixture
def scripts() -> Iterator[Callable[[str], ModuleType]]:
    with release_scripts() as load:
        yield load


@pytest.fixture
def releasedata(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("releasedata")


@pytest.fixture
def collectprs(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("collectprs")


@pytest.fixture
def builditems(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("builditems")


@pytest.fixture
def extractreport(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("extractreport")


@pytest.fixture
def renderentry(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("renderentry")


@pytest.fixture
def validateentry(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("validateentry")


@pytest.fixture
def announcement(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("announcement")


@pytest.fixture
def textsafety(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("textsafety")


@pytest.fixture
def reportschema(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("reportschema")


@pytest.fixture
def postslack(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("postslack")


@pytest.fixture
def webhooks(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("webhooks")


@pytest.fixture
def postdiscord(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("postdiscord")


@pytest.fixture
def repo(tmp_path: Path, collectprs: ModuleType) -> ThrowawayRepo:
    return ThrowawayRepo(tmp_path / "repo", collectprs.RELEASE_PLEASE_CONFIG)


@pytest.fixture
def peer() -> Iterator[WebhookPeer]:
    with webhook_peer() as running:
        yield running
    assert running.replies == [], "a scripted reply was never requested"


@pytest.fixture
def make_item(releasedata: ModuleType) -> Callable[..., object]:
    """Build a report item; ``category`` is a ``Category`` value."""

    def build(
        category: str, label: str, sentence: str, sources: tuple[str, ...] = ("101",)
    ) -> object:
        return releasedata.Item(
            category=category, label=label, sentence=sentence, sources=list(sources)
        )

    return build


@pytest.fixture
def make_notes(releasedata: ModuleType) -> Callable[..., object]:
    """Build the release notes of ``RELEASE_TAG`` for the Python SDK."""

    def build(items: list[object], breaking: object = None) -> object:
        return releasedata.ReleaseNotes(
            tag=RELEASE_TAG,
            previous_tag=PREVIOUS_TAG,
            release_url=RELEASE_URL,
            release_date=RELEASE_DATE,
            product=PRODUCT,
            items=items,
            breaking=breaking,
        )

    return build
