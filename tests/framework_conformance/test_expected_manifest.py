"""The expected-cell manifest file round-trips cells and fails loudly on bad input."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tests.e2e.baseline.scorecard import (
    ALL_LANES,
    SUITE_ADAPTER,
    ExpectedCell,
    dump_manifest,
    load_manifest,
)
from tests.e2e.baseline.toolkit.ci_lanes import known_lane_ids
from tests.framework_conformance.scorecardkit import (
    _ADAPTER_A,
    _ADAPTER_B,
    _LANE_A,
    _LANE_B,
)

_EVERY_LANE = tuple(sorted(known_lane_ids()))
_HOME_A = str(_LANE_A.id)
_HOME_B = str(_LANE_B.id)
_OWNER = "smoke/m.py::TestM"
_TEST = f"tests/e2e/baseline/{_OWNER}::test_x"

_SHAPES = {
    "every-lane": [ExpectedCell(_TEST, SUITE_ADAPTER, _EVERY_LANE, "pass")],
    "one-lane": [ExpectedCell(_TEST, SUITE_ADAPTER, (_HOME_A,), "pass")],
    "matrix": [
        ExpectedCell(_TEST, _ADAPTER_A, (_HOME_A,), "pass"),
        ExpectedCell(_TEST, _ADAPTER_B, _EVERY_LANE, "na", "no dialect"),
    ],
    "lane-pin": [
        ExpectedCell(_TEST, _ADAPTER_A, (_HOME_B,), "pass"),
        ExpectedCell(_TEST, _ADAPTER_B, (_HOME_B,), "pass"),
    ],
    "env-gated-suite": [
        ExpectedCell(_TEST, SUITE_ADAPTER, _EVERY_LANE, "na", "flag off")
    ],
    "params-with-separator": [
        ExpectedCell(f"{_TEST}[a::b]", SUITE_ADAPTER, _EVERY_LANE, "pass")
    ],
}


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


class TestExpectedManifest:
    @pytest.mark.parametrize("cells", _SHAPES.values(), ids=_SHAPES.keys())
    def test_cells_round_trip(self, tmp_path: Path, cells: list[ExpectedCell]) -> None:
        path = tmp_path / "manifest.yaml"
        dump_manifest(cells, path)
        assert set(load_manifest(path)) == set(cells)

    def test_registry_lanes_are_left_out_of_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.yaml"
        dump_manifest(
            [
                *_SHAPES["matrix"],
                ExpectedCell(f"{_TEST}_too", SUITE_ADAPTER, _EVERY_LANE, "pass"),
            ],
            path,
        )
        assert yaml.safe_load(path.read_text()) == {
            _OWNER: {
                "test_x": {
                    "pass": [_ADAPTER_A],
                    "na": [{"reason": "no dialect", "adapters": [_ADAPTER_B]}],
                },
                "test_x_too": ALL_LANES,
            }
        }

    def test_adapter_list_stays_on_one_line(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.yaml"
        dump_manifest(_SHAPES["lane-pin"], path)
        adapters = ", ".join(sorted((_ADAPTER_A, _ADAPTER_B)))
        assert f"    pass: [{adapters}]\n" in path.read_text()

    @pytest.mark.parametrize(
        "spec",
        [
            {"pass": [_ADAPTER_A], "skip": [_ADAPTER_B]},
            "no-such-lane",
            {"pass": ["no-such-adapter"]},
            {"na": [{"reason": "why", "adapters": ["no-such-adapter"]}]},
            {"pass": [SUITE_ADAPTER]},
            {"lane": ALL_LANES, "pass": [_ADAPTER_A]},
        ],
        ids=[
            "unknown-key",
            "unknown-lane",
            "unknown-adapter",
            "unknown-na-adapter",
            "suite-pass",
            "pin-all",
        ],
    )
    def test_load_rejects_an_invalid_entry(self, tmp_path: Path, spec: object) -> None:
        path = _write(tmp_path, {_OWNER: {"test_x": spec}})
        with pytest.raises(ValidationError):
            load_manifest(path)

    @pytest.mark.parametrize(
        "cells",
        [
            [ExpectedCell(_TEST, _ADAPTER_A, (_HOME_A,), "na", "lane-scoped na")],
            [
                ExpectedCell(_TEST, _ADAPTER_A, (_HOME_B,), "pass"),
                ExpectedCell(_TEST, _ADAPTER_B, (_HOME_A,), "pass"),
            ],
            [
                ExpectedCell(
                    "tests/other/m.py::test_x", SUITE_ADAPTER, _EVERY_LANE, "pass"
                )
            ],
        ],
        ids=["na-in-one-lane", "two-pins", "outside-baseline"],
    )
    def test_dump_refuses_cells_the_format_cannot_express(
        self, tmp_path: Path, cells: list[ExpectedCell]
    ) -> None:
        with pytest.raises(ValueError):
            dump_manifest(cells, tmp_path / "manifest.yaml")
