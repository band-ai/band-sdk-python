"""Host preflight performs configured RPCs without claiming a room or turn."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.integrations.codex.stdio_client import CodexStdioClient
from tests.adapters.codex.probes import CLOSE_FAILURE, PROBE_MARKER, ProbeHost


class TestPreflight:
    async def test_configured_handshake_checks_login_without_room_or_turn(
        self, probe_host: ProbeHost, tmp_path: Path
    ) -> None:
        skill_root = str(tmp_path / "skills")
        adapter = probe_host.adapter(
            replies={"account/read": {"account": {"type": "chatgpt"}}},
            client_name="host_name",
            client_title="Host title",
            client_version="2.3.4",
            experimental_api=False,
            skill_roots=[skill_root],
        )

        result = await adapter.preflight(timeout_s=5)

        assert result.ok
        assert probe_host.methods == [
            "initialize",
            "initialized",
            "skills/extraRoots/set",
            "account/read",
        ]
        initialize = probe_host.messages[0]
        assert initialize["params"] == {
            "clientInfo": {
                "name": "host_name",
                "title": "Host title",
                "version": "2.3.4",
            },
            "capabilities": {"experimentalApi": False},
        }
        assert initialize["marker"] == PROBE_MARKER
        assert probe_host.messages[2]["params"] == {"extraRoots": [skill_root]}
        assert adapter._room_clients == {}
        assert adapter._workspaces.rooms == ()
        probe_host.assert_exited()

    @pytest.mark.parametrize(
        ("account", "ok"),
        [
            ({"account": None, "requiresOpenaiAuth": True}, False),
            ({"account": None, "requiresOpenaiAuth": False}, True),
            ({"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}, True),
            ({"error": {"code": -32601, "message": "unsupported"}}, True),
            ({"error": {"code": -32000, "message": "refused"}}, True),
        ],
        ids=[
            "missing-required-login",
            "custom-provider",
            "api-key",
            "unsupported",
            "refused",
        ],
    )
    async def test_only_positively_missing_required_auth_fails(
        self, probe_host: ProbeHost, account: dict, ok: bool
    ) -> None:
        adapter = probe_host.adapter(replies={"account/read": account})

        result = await adapter.preflight(timeout_s=5)

        assert result.ok is ok
        if not ok:
            assert "not logged in" in result.reason
            assert "codex login" in result.remedy
        probe_host.assert_exited()

    async def test_missing_executable_names_install_remedy(
        self, tmp_path: Path
    ) -> None:
        adapter = CodexAdapter(
            CodexAdapterConfig(codex_command=(str(tmp_path / "absent-codex"),))
        )

        result = await adapter.preflight(timeout_s=5)

        assert not result.ok
        assert "binary not found" in result.reason
        assert "codex_command" in result.remedy
        await adapter.cleanup_all()

    @pytest.mark.parametrize("method", ["initialize", "account/read"])
    async def test_hung_rpc_has_a_budget_and_reaps_the_owned_process(
        self, probe_host: ProbeHost, method: str
    ) -> None:
        adapter = probe_host.adapter(replies={method: {"hang": True}})

        result = await adapter.preflight(timeout_s=1)

        assert not result.ok
        assert result.reason
        probe_host.assert_exited()

    async def test_initialize_error_is_a_failed_result_after_cleanup(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(
            replies={"initialize": {"error": {"code": -32000, "message": "bad frame"}}}
        )

        result = await adapter.preflight(timeout_s=5)

        assert not result.ok
        assert "handshake" in result.reason
        assert result.remedy
        probe_host.assert_exited()

    async def test_cancellation_propagates_and_reaps_the_process(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(replies={"account/read": {"hang": True}})
        probing = asyncio.create_task(adapter.preflight(timeout_s=5))
        try:
            await probe_host.received("account/read")
            probing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await probing
            await adapter.cleanup_all()
            probe_host.assert_exited()
        finally:
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)

    async def test_cleanup_failure_remains_owned_for_adapter_retry(
        self, probe_host: ProbeHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = probe_host.adapter()

        async def refuse(_client: CodexStdioClient) -> None:
            raise RuntimeError(CLOSE_FAILURE)

        with monkeypatch.context() as patch:
            patch.setattr(CodexStdioClient, "_close_process", refuse)
            with pytest.raises(CleanupIncompleteError):
                await adapter.preflight(timeout_s=5)
            [client] = probe_host.clients
            assert client._proc is not None and client._proc.returncode is None

        await adapter.cleanup_all()
        probe_host.assert_exited()

    async def test_second_preflight_uses_a_fresh_process(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter()

        first = await adapter.preflight(timeout_s=5)
        second = await adapter.preflight(timeout_s=5)

        assert first.ok and second.ok
        assert len(probe_host.clients) == 2
        probe_host.assert_exited()
