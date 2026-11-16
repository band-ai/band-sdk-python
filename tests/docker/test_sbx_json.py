"""``sbx_json`` reads the document an ``sbx --json`` command printed, banner or not."""

from __future__ import annotations

import json

import pytest

from band.docker.sbx_process import sbx_json
from tests.docker.sbx_banner import UPDATE_BANNER

POLICY_CHECK = {"allowed": True, "target": "api.dev.band.ai:443", "type": "network"}


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
