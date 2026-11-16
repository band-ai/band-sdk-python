"""A local workload is a kit directory sbx accepts that builds the given image."""

from __future__ import annotations

import re

import yaml

from tests.docker.toolkit.sbx_cli import local_workload

IMAGE = "band-python-kit:test"
# sbx answers `unknown agent ... (built-in agents only)` for a kit directory
# whose name carries an `_`; tempfile's random suffix often does.
SBX_AGENT_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


class TestLocalWorkload:
    def test_workload_directory_is_a_valid_sbx_agent_name(self) -> None:
        names = set()
        for _ in range(50):
            with local_workload(IMAGE) as kit:
                names.add(kit.name)

        assert all(SBX_AGENT_NAME.fullmatch(name) for name in names), names

    def test_workload_descriptor_builds_from_the_image(self) -> None:
        with local_workload(IMAGE) as kit:
            descriptor = yaml.safe_load(
                (kit / "band-workload.yaml").read_text(encoding="utf-8")
            )

        assert descriptor["build"] == f"FROM {IMAGE}\n"
        assert "dockerfile" not in descriptor
