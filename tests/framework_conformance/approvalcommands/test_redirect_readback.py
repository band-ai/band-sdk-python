"""Shell redirect readback decodes every host encoding."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from tests.e2e.baseline.smoke.samples.approvals import written_lines


class TestRedirectReadback:
    @pytest.mark.parametrize(
        ("encoding", "bom"),
        [
            ("utf-8", b""),
            ("utf-8", codecs.BOM_UTF8),
            ("utf-16-le", codecs.BOM_UTF16_LE),
        ],
        ids=["utf8", "utf8-bom", "utf16-le-bom"],
    )
    def test_shell_redirect_readback_handles_host_encodings(
        self, tmp_path: Path, encoding: str, bom: bytes
    ) -> None:
        target = tmp_path / "policy.txt"
        marker = "policy-marker"
        target.write_bytes(bom + f"{marker} \r\n".encode(encoding))

        assert written_lines(target) == [marker]
