"""The posters read the message as UTF-8 from a file or stdin and refuse blank text."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

MESSAGE = "*Band Python SDK v6.1.0* is live! — ünïcode"


class TestReadMessage:
    def test_stdin_is_read_as_utf8(
        self, webhooks: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdin = io.TextIOWrapper(io.BytesIO(MESSAGE.encode("utf-8")), encoding="cp1252")
        monkeypatch.setattr(sys, "stdin", stdin)
        assert webhooks.read_message(None) == MESSAGE

    def test_file_is_read_as_utf8(self, webhooks: ModuleType, tmp_path: Path) -> None:
        message = tmp_path / "message.txt"
        message.write_bytes(MESSAGE.encode("utf-8"))
        assert webhooks.read_message(message) == MESSAGE

    def test_blank_message_is_refused(
        self, webhooks: ModuleType, tmp_path: Path
    ) -> None:
        message = tmp_path / "message.txt"
        message.write_text(" \n", encoding="utf-8")
        with pytest.raises(ValueError, match="message text is required"):
            webhooks.read_message(message)
