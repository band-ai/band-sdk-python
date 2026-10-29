"""Antigravity command settings retain exact JSON argv."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests.e2e.baseline.settings import Backends


class TestAntigravitySettings:
    def test_json_command_preserves_spaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = ("native installation/server.exe", "argument with spaces", "--uid=")
        monkeypatch.setenv("ANTIGRAVITY_COMMAND", json.dumps(command))

        assert Backends().antigravity_command == command

    @pytest.mark.parametrize("command", [(), ("",), ("   ",), ("server", "")])
    def test_explicit_command_rejects_empty_entries(
        self, command: tuple[str, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="nonempty argv"):
            Backends(antigravity_command=command)

    def test_unset_command_keeps_sdk_default_selection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTIGRAVITY_COMMAND", raising=False)

        assert Backends().antigravity_command is None
