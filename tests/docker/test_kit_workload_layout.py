"""Proves the multi-arch band-python-kit OCI layout passes workload validation.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py); CI's docker-kit job
opts in. Fixtures live in tests/docker/conftest.py /
tests/docker/toolkit/ — this file is scenario only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.docker.toolkit.docker_cli import BUILD_TEST_TIMEOUT_S, OciLayout


@pytest.mark.docker_build
@pytest.mark.timeout(BUILD_TEST_TIMEOUT_S)
class TestKitWorkloadLayoutConformance:
    def test_workload_graph_conforms_on_both_architectures(
        self, band_python_kit_layout: OciLayout, kit_tck: Path
    ) -> None:
        band_python_kit_layout.validate(tck=kit_tck)
