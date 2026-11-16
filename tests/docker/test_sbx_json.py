"""``sbx_json`` reads the document an ``sbx --json`` command printed, banner or not."""

from __future__ import annotations

import json

import pytest

from band.docker.sbx_process import sbx_json

POLICY_CHECK = {"allowed": True, "target": "api.dev.band.ai:443", "type": "network"}

# Captured from sbx v0.47.0's stdout after `policy check network --json`, on the
# run where its daily update check found v0.53.0.
UPDATE_BANNER = (
    "\n"
    "╭──────────────────────────────────────────────────────────────────────────────────╮\n"
    "│ Docker Sandboxes Update Available                                                │\n"
    "├──────────────────────────────────────────────────────────────────────────────────┤\n"
    "│ v0.47.0  →  v0.53.0                                                              │\n"
    "├──────────────────────────────────────────────────────────────────────────────────┤\n"
    "│ Release notes  https://github.com/docker/sbx-releases/releases/tag/v0.53.0       │\n"
    "├──────────────────────────────────────────────────────────────────────────────────┤\n"
    "│ To upgrade     brew upgrade docker/tap/sbx                                       │\n"
    "╰──────────────────────────────────────────────────────────────────────────────────╯\n"
)


class TestSbxJson:
    @pytest.mark.parametrize(
        "trailer",
        [
            pytest.param("\n", id="document-only"),
            pytest.param("\n" + UPDATE_BANNER + "\n", id="update-banner"),
        ],
    )
    def test_reads_the_leading_document(self, trailer: str) -> None:
        stdout = json.dumps(POLICY_CHECK, indent=2) + trailer

        assert sbx_json(stdout) == POLICY_CHECK

    def test_output_without_a_document_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            sbx_json(UPDATE_BANNER)
