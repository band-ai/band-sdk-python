"""Committed unit-test durations for in-job xdist scheduling.

CI does not add GitHub Actions shards. Each runner already has 4 vCPUs;
xdist worksteal uses them. This module keeps those workers busy by starting
historically slow tests first, and records a fresh map when
``--band-store-durations`` is passed (the weekly / on-demand refresh
workflow).

xdist workers cannot share one durations file: each writes a ``.gwN`` sidecar
and the controller merges. ``--band-clean-durations`` drops nodeids the run
did not see, so renamed or deleted tests do not linger.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Final

import pytest

from tests.xdistworker import SERIAL_WORKER_ID

logger = logging.getLogger(__name__)

DURATIONS_PATH_NAME: Final = ".test_durations"
DURATION_QUANTUM: Final = 0.1
STORE_FLAG: Final = "--band-store-durations"
CLEAN_FLAG: Final = "--band-clean-durations"
PATH_FLAG: Final = "--band-durations-path"
_RECORDED = pytest.StashKey[dict[str, float]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("band-durations")
    group.addoption(
        STORE_FLAG,
        action="store_true",
        default=False,
        help=f"Write call timings to {PATH_FLAG} at session end.",
    )
    group.addoption(
        CLEAN_FLAG,
        action="store_true",
        default=False,
        help="Drop nodeids the current run did not execute.",
    )
    group.addoption(
        PATH_FLAG,
        default=DURATIONS_PATH_NAME,
        help="JSON map of nodeid to seconds used for slowest-first scheduling.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_RECORDED] = {}


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Start historically slow tests first so xdist workers saturate early."""
    durations = load_durations(Path(config.getoption("band_durations_path")))
    if not durations:
        return
    items.sort(key=lambda item: nodeid_slowness(item.nodeid, durations))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if item.config.getoption("band_store_durations"):
        recorded = item.config.stash[_RECORDED]
        recorded[item.nodeid] = recorded.get(item.nodeid, 0.0) + call.duration
    return report


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if not config.getoption("band_store_durations"):
        return
    path = Path(config.getoption("band_durations_path"))
    recorded = config.stash[_RECORDED]
    worker = session_xdist_worker_id(config)
    if worker is not None:
        write_durations(worker_duration_path(path, worker), recorded)
        return
    worker_files = tuple(path.parent.glob(f"{path.name}.gw*"))
    merged = merge_duration_maps(
        recorded,
        *(load_durations(file) for file in worker_files),
        existing=load_durations(path),
        clean=bool(config.getoption("band_clean_durations")),
    )
    write_durations(path, merged)
    logger.info("wrote %s timings to %s", len(merged), path)
    for file in worker_files:
        file.unlink(missing_ok=True)


def quantize_duration(seconds: float) -> float:
    """Bucket timings so weekly jitter does not open a no-op PR."""
    return round(seconds / DURATION_QUANTUM) * DURATION_QUANTUM


def session_xdist_worker_id(config: object) -> str | None:
    """Worker id for *this* pytest session, ignoring a leaked parent env.

    ``PYTEST_XDIST_WORKER`` is inherited by nested pytest (pytester, pin-guard
    subprocesses). Durations must key off this session's ``workerinput`` or a
    child would write a sidecar and never merge.
    """
    workerinput = getattr(config, "workerinput", None)
    if not isinstance(workerinput, dict):
        return None
    worker = workerinput.get("workerid")
    if not worker or worker == SERIAL_WORKER_ID:
        return None
    return str(worker)


def worker_duration_path(path: Path, worker: str) -> Path:
    """Sidecar the controller merges after each xdist worker exits."""
    return path.with_name(f"{path.name}.{worker}")


def load_durations(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {str(nodeid): float(seconds) for nodeid, seconds in payload.items()}


def write_durations(path: Path, durations: Mapping[str, float]) -> None:
    path.write_text(
        json.dumps(dict(sorted(durations.items())), indent=2) + "\n",
        encoding="utf-8",
    )


def merge_duration_maps(
    *maps: Mapping[str, float],
    existing: Mapping[str, float] | None = None,
    clean: bool = False,
) -> dict[str, float]:
    """Union worker maps, optionally replacing rather than updating ``existing``."""
    merged: dict[str, float] = {} if clean else dict(existing or {})
    for mapping in maps:
        merged.update(mapping)
    return {nodeid: quantize_duration(seconds) for nodeid, seconds in merged.items()}


def nodeid_slowness(nodeid: str, durations: Mapping[str, float]) -> float:
    """Sort key: historically slower tests come first; unknown nodeids last."""
    return -durations.get(nodeid, 0.0)


def slowest_first(nodeids: list[str], durations: Mapping[str, float]) -> list[str]:
    """Stable reorder: slower nodeids first, equal durations keep incoming order."""
    return sorted(nodeids, key=lambda nodeid: nodeid_slowness(nodeid, durations))
