"""Reject invalid private journal values and parser exhaustion during replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_agent.operations import recorded
from remote_agent.protocol import decode_journal
from remote_agent.storage import StoreError, canonical


class TestJournalJson:
    @pytest.mark.parametrize(
        "encoded",
        [
            b'{"value":1,"value":2}',
            b'{"value":1.5}',
            b'{"value":NaN}',
            b'{"value":9007199254740992}',
            b'{"value":"\\ud800"}',
        ],
    )
    def test_private_decoder_preserves_protocol_value_constraints(
        self, encoded: bytes
    ) -> None:
        with pytest.raises((ValueError, UnicodeError)):
            decode_journal(encoded)

    @pytest.mark.contract_linux
    @pytest.mark.parametrize("location", ["header", "record"])
    def test_archive_parser_exhaustion_is_a_verified_input_failure(
        self, tmp_path: Path, location: str
    ) -> None:
        deeply_nested = b'{"value":' + b"[" * 10_000 + b"0" + b"]" * 10_000 + b"}\n"
        header = canonical(
            {
                "profile": "remote-agent/journal-export/1",
                "source_schema": 1,
                "records": 1,
                "head": "0" * 64,
            }
        )
        archive = tmp_path / "exhausted.jsonl"
        archive.write_bytes(
            deeply_nested if location == "header" else header + b"\n" + deeply_nested
        )
        archive.chmod(0o600)
        with pytest.raises(StoreError, match="could not be verified") as failure:
            recorded(archive, "unused")
        assert isinstance(failure.value.__cause__, RecursionError)
