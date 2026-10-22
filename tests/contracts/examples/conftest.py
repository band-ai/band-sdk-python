from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.contracts.examples.kit import EngineFactory


@pytest.fixture
def engines(tmp_path: Path) -> Iterator[EngineFactory]:
    factory = EngineFactory(tmp_path / "engines")
    try:
        yield factory
    finally:
        factory.dispose()
