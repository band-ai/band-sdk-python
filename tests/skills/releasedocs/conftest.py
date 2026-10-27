"""Fixtures for the release-docs skill's script tests."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.fernnames import FernNames
from tests.skills.releasedocs.fernremote import FernRemote
from tests.skills.releasedocs.ghpeer import FakeGh
from tests.skills.releasedocs.gitrepo import ThrowawayRepo
from tests.skills.releasedocs.holdbackfiles import Inputs
from tests.skills.releasedocs.samples import (
    DOCS_PAGE,
    DOCS_PAGE_TEXT,
    HELD_PR,
    PREVIOUS_TAG,
    PRODUCT,
    RECONNECT,
    RELEASE_DATE,
    RELEASE_TAG,
    RELEASE_URL,
    RELEASED_LEDGER,
    SEND_RETRIES,
    TOKEN_REDACTION,
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
def docsrange(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("docsrange")


@pytest.fixture
def holdbacks(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("holdbacks")


@pytest.fixture
def withhold(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("withhold")


@pytest.fixture
def fernstate(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("fernstate")


@pytest.fixture
def checkpatch(scripts: Callable[[str], ModuleType]) -> ModuleType:
    """The edit check, once its MDX reader can run; CI installs it for every test job."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    module = scripts("checkpatch")
    reader = module.MDX_READER.parent
    if not (reader / "node_modules").is_dir():
        pytest.fail(
            f"Install the MDX reader: npm ci --ignore-scripts --prefix {reader}"
        )
    return module


@pytest.fixture
def docsreport(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("docsreport")


@pytest.fixture
def filefern(scripts: Callable[[str], ModuleType]) -> ModuleType:
    return scripts("filefern")


@pytest.fixture
def fern_files(releasedata: ModuleType) -> dict[str, str | bytes]:
    """The Fern files the filing reads: the ledger, a page, and a date file."""
    return {
        releasedata.HELD_ITEMS: RELEASED_LEDGER,
        DOCS_PAGE: DOCS_PAGE_TEXT,
        f"{releasedata.CHANGELOG_DIR}/2026-10-13.md": "---\ntags: []\n---\n",
    }


@pytest.fixture
def fern_names(releasedata: ModuleType) -> FernNames:
    return FernNames.derive(releasedata)


@pytest.fixture
def fern_remote(
    tmp_path: Path, releasedata: ModuleType, fern_files: dict[str, str | bytes]
) -> FernRemote:
    return FernRemote(tmp_path / "remote", releasedata.FERN_BASE, fern_files)


@pytest.fixture
def gh(releasedata: ModuleType, fern_remote: FernRemote) -> FakeGh:
    return FakeGh(releasedata.FERN_REPO, releasedata.FERN_BASE, fern_remote)


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


@pytest.fixture
def inputs(
    tmp_path: Path, make_item: Callable[..., object], make_notes: Callable[..., object]
) -> Inputs:
    """Three items; the second cites the PR the ledger holds from sdks."""
    notes = make_notes(
        [
            make_item(*SEND_RETRIES, sources=("101",)),
            make_item(*RECONNECT, sources=(HELD_PR,)),
            make_item(*TOKEN_REDACTION, sources=("102",)),
        ]
    )
    return Inputs(tmp_path, notes.model_dump_json())  # type: ignore[attr-defined]
