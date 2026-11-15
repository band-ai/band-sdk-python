"""The isolated helper preserves target environment and provider stdin."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from band.integrations.codex.winspawn import (
    _READY,
    HELPER_PATH,
    WindowsProcess,
    encode_environment,
    environment_block,
)
from tests.integrations.codex.lifecycle import WindowsLifecycle


class TestWindowsTargetEnvironment:
    @pytest.mark.parametrize(
        "env", [{}, {"UNICODE": "שלום🌍", "MULTILINE": "a\nb=c", "EMPTY": ""}]
    )
    def test_private_prelude_does_not_consume_provider_input(
        self, env: dict[str, str]
    ) -> None:
        provider_input = "provider-rpc\n"
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(Path(__file__).with_name("environmentpeer.py")),
                str(HELPER_PATH.with_name("winspawn.py")),
                provider_input,
            ],
            input=encode_environment(env) + provider_input.encode("ascii"),
            capture_output=True,
            timeout=10,
            check=True,
        )

        assert json.loads(completed.stdout) == [env, provider_input]

    @pytest.mark.parametrize(
        "env, expected",
        [
            ({}, "\0\0"),
            ({"z": "a=b\nc", "A": "שלום", "EMPTY": ""}, "A=שלום\0EMPTY=\0z=a=b\nc\0\0"),
        ],
    )
    def test_unicode_environment_block_preserves_complete_values(
        self, env: dict[str, str], expected: str
    ) -> None:
        assert environment_block(env) == expected

    @pytest.mark.parametrize("env", [None, {}, {"EXPLICIT": "value"}])
    async def test_target_environment_is_snapshotted_before_helper_startup(
        self,
        windows_lifecycle: WindowsLifecycle,
        monkeypatch: pytest.MonkeyPatch,
        env: dict[str, str] | None,
    ) -> None:
        peer = windows_lifecycle
        for name in tuple(os.environ):
            monkeypatch.delenv(name)
        monkeypatch.setenv("ENVIRONMENT_SNAPSHOT", "before")
        expected = dict(os.environ if env is None else env)
        peer.creation_allowed.clear()
        owner = WindowsProcess(create_process=peer.launch)
        starting = asyncio.create_task(
            owner.start_command([sys.executable], cwd=None, env=env, limit=65536)
        )
        try:
            await peer.creation_started.wait()
            monkeypatch.setenv("ENVIRONMENT_SNAPSHOT", "after")
            peer.creation_allowed.set()
            peer.status.set_result(_READY)
            await starting

            assert json.loads(peer.helper.stdin.written) == expected
        finally:
            peer.creation_allowed.set()
            await owner.close(0)
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)

    async def test_close_cancels_blocked_environment_delivery_and_releases_ownership(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        peer.helper.stdin.drain_allowed.clear()
        owner = WindowsProcess(create_process=peer.launch)
        starting = asyncio.create_task(
            owner.start_command([sys.executable], cwd=None, env={}, limit=65536)
        )
        try:
            await peer.helper.stdin.drain_started.wait()
            await owner.close(0)

            with pytest.raises(asyncio.CancelledError):
                await starting
            assert peer.helper.returncode is not None
            assert peer.job.closed
        finally:
            peer.helper.stdin.drain_allowed.set()
            await owner.close(0)
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
